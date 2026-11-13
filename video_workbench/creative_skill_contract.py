"""Creative v5: frozen Skill resources and independently authenticated reading.

The gateway must obtain result/evidence from the authenticated signed Agent
handoff. Hashes and a self-declared source string cannot authenticate arbitrary
client JSON. This contract binds custody; it never approves rendered quality.
"""
from copy import deepcopy
import hashlib

from agent_service.contracts import canonical_bytes, digest
from agent_service.profiles.video_animation_skill_production import (
    PROFILE, INSTRUCTION_REVISION, BUNDLE_REVISION, BUNDLE_MANIFEST_SHA256,
    VideoAnimationSkillProductionProfile,
)
from agent_service.profiles.video_animation_skill_production_contract import (
    INPUT_SCHEMA, DECISION_SCOPE, MAX_TOOL_EVIDENCE_BYTES, require, validate_input, validate_result,
)
from agent_service.profiles.video_animation_production_contract import validate_core_task
from agent_service.skill_tool_evidence import validate_tool_evidence
from .animation_skill_production import validate_delivery
from .motion import validate_alignment


CREATIVE_SCHEMA = 'video.creative-scene.v5'
ADAPTER_REVISION = 'creative-skill-film-scene-adapter-1'
MAX_PACKAGE_BYTES = 40 * 1024 * 1024
PACKAGE_FIELDS = frozenset({'schema', 'adapter_revision', 'input_document', 'input_sha256',
    'profile', 'result', 'result_sha256', 'engine', 'skill_delivery', 'skill_delivery_sha256',
    'skill_catalog_sha256', 'skill_tool_evidence', 'skill_tool_evidence_sha256',
    'skill_decisions', 'evidence_scope'})


def build_scene_input(core_task, skill_delivery):
    """The main Agent input has only the lightweight directory, never bodies."""
    core = validate_core_task(core_task, engine_aware=True, film_aware=True)
    delivery = validate_delivery(skill_delivery)
    return validate_scene_input(dict(schema=INPUT_SCHEMA, **core, skill_catalog=delivery['catalog']))


def validate_scene_input(document):
    document = validate_input(document)
    validate_alignment(document['scene']['alignment'], document['scene']['beats'])
    return document


def collect_scene_result(input_document, result, evidence, *, skill_delivery):
    """Bind authenticated stopped artifacts to the exact durable resource set."""
    document = validate_scene_input(input_document)
    input_sha256 = digest(document)
    delivery = validate_delivery(skill_delivery)
    require(delivery['catalog'] == document['skill_catalog'], 'creative_skill_delivery_binding_mismatch', 409)
    require(type(evidence) is dict and type(evidence.get('attempt_id')) is str,
            'creative_skill_host_evidence_required', 409)
    evidence = validate_tool_evidence(evidence, delivery['catalog'], delivery['packages'],
        attempt_id=evidence['attempt_id'], input_sha256=input_sha256)
    require(len(canonical_bytes(evidence)) <= MAX_TOOL_EVIDENCE_BYTES,
            'creative_skill_evidence_too_large', 413)
    reads = {event['receipt']['package_sha256'] for event in evidence['events']
             if event['tool_name'] == 'skill_read' and event['receipt']['path'] == 'SKILL.md'}
    output = validate_result(result, input_sha256, document,
        read_package_sha256s=reads, tool_evidence_sha256=evidence['sha256'])
    profile = dict(name=PROFILE, revision='1', instruction_revision=INSTRUCTION_REVISION,
        bundle_revision=BUNDLE_REVISION, bundle_manifest_sha256=BUNDLE_MANIFEST_SHA256,
        prompt_sha256=hashlib.sha256(VideoAnimationSkillProductionProfile().build_prompt(
            {'input_sha256': input_sha256}).encode('utf-8')).hexdigest())
    package = dict(schema=CREATIVE_SCHEMA, adapter_revision=ADAPTER_REVISION,
        input_document=document, input_sha256=input_sha256, profile=profile,
        result=output, result_sha256=digest(output), engine=output['source']['engine'],
        skill_delivery=delivery, skill_delivery_sha256=delivery['sha256'],
        skill_catalog_sha256=document['skill_catalog']['sha256'],
        skill_tool_evidence=evidence, skill_tool_evidence_sha256=evidence['sha256'],
        skill_decisions=deepcopy(output['skill_decisions']), evidence_scope=DECISION_SCOPE)
    require(len(canonical_bytes(package)) <= MAX_PACKAGE_BYTES, 'creative_skill_package_too_large', 413)
    return package


def validate_scene_package(package):
    require(type(package) is dict and set(package) == PACKAGE_FIELDS
            and package['schema'] == CREATIVE_SCHEMA and package['adapter_revision'] == ADAPTER_REVISION)
    expected = collect_scene_result(package['input_document'], package['result'], package['skill_tool_evidence'],
                                    skill_delivery=package['skill_delivery'])
    require(package == expected, 'creative_skill_package_changed', 409)
    return expected


def scene_context(package):
    return validate_scene_package(package)['input_document']['scene']


def scene_source(package):
    return validate_scene_package(package)['result']['source']


def scene_files(package):
    return {name: text.encode('utf-8') for name, text in scene_source(package)['files'].items()}


build_production_input = build_scene_input
validate_production_input = validate_scene_input
collect_production_result = collect_scene_result
validate_production_package = validate_scene_package
