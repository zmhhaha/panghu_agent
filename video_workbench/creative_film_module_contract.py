"""Frozen original film modules, independently bound to actual Skill reads.

No legacy scene package conversion, module execution, render or human approval.
The application additionally checks module identities against the film compiler.
"""
from copy import deepcopy
import hashlib

from agent_service.contracts import canonical_bytes, digest
from agent_service.profiles.video_animation_film_module import (
    PROFILE, INSTRUCTION_REVISION, BUNDLE_REVISION, BUNDLE_MANIFEST_SHA256,
    VideoAnimationFilmModuleProfile,
)
from agent_service.profiles.video_animation_film_module_contract import (
    INPUT_SCHEMA, CONTEXT_SCHEMA, CORE_FIELDS, DECISION_SCOPE, MAX_TOOL_EVIDENCE_BYTES,
    require, validate_core_task, validate_input, validate_result, module_metadata,
)
from agent_service.skill_tool_evidence import validate_tool_evidence
from .animation_skill_production import validate_delivery
from .hyperframes_film_project import module_identity
from .motion import validate_alignment

CREATIVE_SCHEMA = 'video.creative-film-module.v1'
ADAPTER_REVISION = 'creative-hyperframes-composable-module-1'
MAX_PACKAGE_BYTES = 46 * 1024 * 1024
PACKAGE_FIELDS = frozenset({'schema', 'adapter_revision', 'input_document', 'input_sha256',
    'profile', 'result', 'result_sha256', 'engine', 'skill_delivery', 'skill_delivery_sha256',
    'skill_catalog_sha256', 'skill_tool_evidence', 'skill_tool_evidence_sha256',
    'skill_decisions', 'evidence_scope'})


def prepare_core_task(*, scene_id, title, visual_description, narration, beats, alignment,
                      audio_sha256, duration_seconds, film_context, module_id, namespace,
                      runtime_identity, transition_target=None, width=1280, height=720, fps=24,
                      assets=None, instruction='', base_module=None, style='', allowed_engines=None,
                      predecessor_package=None):
    """Exact task for SkillProductionAdapter.freeze, including the new targets."""
    value = dict(scene=dict(scene_id=scene_id, title=title, visual_description=visual_description,
        narration=narration, beats=deepcopy(beats), alignment=deepcopy(alignment),
        audio_sha256=audio_sha256, instruction=instruction, style=style),
        constraints=dict(width=width, height=height, fps=fps, duration_seconds=duration_seconds,
            allowed_engines=['html-svg'] if allowed_engines is None else deepcopy(allowed_engines),
            runtime_identity=deepcopy(runtime_identity)),
        film_context=deepcopy(film_context),
        target=dict(module_id=module_id, namespace=namespace, transition_target=deepcopy(transition_target)),
        assets={} if assets is None else deepcopy(assets), base_module=deepcopy(base_module))
    value = validate_core_task(value)
    validate_alignment(value['scene']['alignment'], value['scene']['beats'])
    if predecessor_package is not None:
        from .film_appearance_context import attach_predecessor_appearance
        value = attach_predecessor_appearance(value, predecessor_package)
    return value


def build_module_input(core_task, skill_delivery):
    core = validate_core_task(core_task)
    delivery = validate_delivery(skill_delivery)
    return validate_module_input(dict(schema=INPUT_SCHEMA, **core, skill_catalog=delivery['catalog']))


def validate_module_input(document):
    document = validate_input(document)
    validate_alignment(document['scene']['alignment'], document['scene']['beats'])
    return document


def collect_module_result(input_document, result, evidence, *, skill_delivery):
    document = validate_module_input(input_document)
    input_sha256 = digest(document)
    delivery = validate_delivery(skill_delivery)
    require(delivery['catalog'] == document['skill_catalog'], 'creative_film_module_delivery_mismatch', 409)
    require(type(evidence) is dict and type(evidence.get('attempt_id')) is str,
            'creative_film_module_host_evidence_required', 409)
    evidence = validate_tool_evidence(evidence, delivery['catalog'], delivery['packages'],
        attempt_id=evidence['attempt_id'], input_sha256=input_sha256)
    require(len(canonical_bytes(evidence)) <= MAX_TOOL_EVIDENCE_BYTES,
            'creative_film_module_evidence_too_large', 413)
    reads = {event['receipt']['package_sha256'] for event in evidence['events']
             if event['tool_name'] == 'skill_read' and event['receipt']['path'] == 'SKILL.md'}
    output = validate_result(result, input_sha256, document,
        read_package_sha256s=reads, tool_evidence_sha256=evidence['sha256'])
    # Application-side pure compiler validation catches drift in worker modules.
    module = output['module']
    require(module_identity(module) == digest(module_metadata(module)),
            'creative_film_module_compiler_identity_mismatch', 409)
    incoming = output['incoming_boundary']
    if incoming is not None:
        require(incoming['boundary']['to_module_sha256'] == module_identity(module),
                'creative_film_module_boundary_mismatch', 409)
        transition = incoming['transition_module']
        if transition is not None:
            require(incoming['boundary']['transition']['module_sha256'] == module_identity(transition),
                    'creative_film_module_boundary_mismatch', 409)
    profile = dict(name=PROFILE, revision='1', instruction_revision=INSTRUCTION_REVISION,
        bundle_revision=BUNDLE_REVISION, bundle_manifest_sha256=BUNDLE_MANIFEST_SHA256,
        prompt_sha256=hashlib.sha256(VideoAnimationFilmModuleProfile().build_prompt(
            {'input_sha256': input_sha256}).encode('utf-8')).hexdigest())
    package = dict(schema=CREATIVE_SCHEMA, adapter_revision=ADAPTER_REVISION,
        input_document=document, input_sha256=input_sha256, profile=profile,
        result=output, result_sha256=digest(output), engine=module['engine'],
        skill_delivery=delivery, skill_delivery_sha256=delivery['sha256'],
        skill_catalog_sha256=document['skill_catalog']['sha256'],
        skill_tool_evidence=evidence, skill_tool_evidence_sha256=evidence['sha256'],
        skill_decisions=deepcopy(output['skill_decisions']), evidence_scope=DECISION_SCOPE)
    require(len(canonical_bytes(package)) <= MAX_PACKAGE_BYTES, 'creative_film_module_package_too_large', 413)
    return package


def validate_module_package(package):
    require(type(package) is dict and set(package) == PACKAGE_FIELDS
        and package['schema'] == CREATIVE_SCHEMA and package['adapter_revision'] == ADAPTER_REVISION,
        'creative_film_module_package_invalid')
    expected = collect_module_result(package['input_document'], package['result'], package['skill_tool_evidence'],
                                    skill_delivery=package['skill_delivery'])
    require(package == expected, 'creative_film_module_package_changed', 409)
    return expected


def module_context(package):
    return validate_module_package(package)['input_document']['scene']


def package_modules(package):
    result = validate_module_package(package)['result']
    modules = [result['module']]
    incoming = result['incoming_boundary']
    if incoming is not None and incoming['transition_module'] is not None:
        modules.append(incoming['transition_module'])
    return modules


def package_boundary(package):
    incoming = validate_module_package(package)['result']['incoming_boundary']
    return None if incoming is None else incoming['boundary']


def module_files(package):
    return {name: source.encode('utf-8') for module in package_modules(package)
            for name, source in module['files'].items()}


def predecessor_from_package(package):
    package = validate_module_package(package)
    module = package['result']['module']
    return dict(module=module_metadata(module), module_sha256=module_identity(module),
                input_sha256=package['input_sha256'], result_sha256=package['result_sha256'])
