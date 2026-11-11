"""Production v2 package: frozen knowledge and explicit author declarations.

This module never renders, accepts a method, or upgrades a delivery receipt to
observed use. Legacy creative contracts and their provenance remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from agent_service.contracts import digest
from agent_service.profiles.video_animation_production import (
    PROFILE, INPUT_SCHEMA, INSTRUCTION_REVISION, BUNDLE_REVISION, BUNDLE_MANIFEST_SHA256,
    VideoAnimationProductionProfile,
)
from agent_service.profiles.video_animation_production_contract import (
    DECISION_SCOPE, require, validate_core_task, validate_input, validate_result,
)
from .animation_method_production import validate_delivery, collect_method_decision
from .motion import validate_alignment


CREATIVE_SCHEMA = 'video.creative-scene.v2'
ADAPTER_REVISION = 'creative-method-scene-adapter-1'
PACKAGE_FIELDS = frozenset({'schema', 'adapter_revision', 'input_document', 'input_sha256',
    'profile', 'result', 'result_sha256', 'engine', 'method_delivery_sha256', 'method_decision', 'evidence_scope'})


def prepare_core_task(*, scene_id, title, visual_description, narration, beats, alignment,
                      audio_sha256, duration_seconds, width=1280, height=720, fps=24,
                      assets=None, instruction='', base_source=None, style=''):
    """The exact dictionary passed to MethodProductionAdapter.freeze as task."""
    base = deepcopy(base_source)
    if type(base) is dict and set(base) == {'engine', 'strategy', 'files'}:
        require(type(base['files']) is dict and all(type(v) is str for v in base['files'].values()))
        base['files_sha256'] = {name: hashlib.sha256(content.encode()).hexdigest()
                                for name, content in base['files'].items()}
    value = dict(scene=dict(scene_id=scene_id, title=title, visual_description=visual_description,
        narration=narration, beats=deepcopy(beats), alignment=deepcopy(alignment),
        audio_sha256=audio_sha256, instruction=instruction, style=style),
        constraints=dict(width=width, height=height, fps=fps, duration_seconds=duration_seconds,
                         allowed_engines=['html-svg']),
        assets={} if assets is None else deepcopy(assets), base_source=base)
    value = validate_core_task(value)
    validate_alignment(value['scene']['alignment'], value['scene']['beats'])
    return value


def build_scene_input(*, core_task, method_delivery):
    core = validate_core_task(core_task)
    delivery = validate_delivery(method_delivery)
    return validate_scene_input(dict(schema=INPUT_SCHEMA, **core, method_delivery=delivery))


def validate_scene_input(document):
    value = validate_input(document)
    require(validate_delivery(value['method_delivery']) == value['method_delivery'])
    validate_alignment(value['scene']['alignment'], value['scene']['beats'])
    return value


def collect_scene_result(input_document, result):
    document = validate_scene_input(input_document)
    checksum = digest(document)
    output = validate_result(result, checksum, document)
    # Independently retain the pre-existing delivery/decision semantics. Targets
    # are checked by the new contract and are still only Agent declarations.
    decision = output['method_decision']
    collect_method_decision(document['method_delivery'], dict(delivery_sha256=decision['delivery_sha256'],
        decisions=[{key: row[key] for key in ('version_id', 'content_sha256', 'action', 'reason')}
                   for row in decision['decisions']]))
    return dict(schema=CREATIVE_SCHEMA, adapter_revision=ADAPTER_REVISION,
        input_document=document, input_sha256=checksum,
        profile=dict(name=PROFILE, revision='1', instruction_revision=INSTRUCTION_REVISION,
            bundle_revision=BUNDLE_REVISION, bundle_manifest_sha256=BUNDLE_MANIFEST_SHA256,
            prompt_sha256=hashlib.sha256(VideoAnimationProductionProfile().build_prompt(
                {'input_sha256': checksum}).encode()).hexdigest()),
        result=output, result_sha256=digest(output), engine=output['source']['engine'],
        method_delivery_sha256=document['method_delivery']['sha256'], method_decision=deepcopy(decision),
        evidence_scope=DECISION_SCOPE)


def validate_scene_package(package):
    require(type(package) is dict and set(package) == PACKAGE_FIELDS
        and package.get('schema') == CREATIVE_SCHEMA and package.get('adapter_revision') == ADAPTER_REVISION)
    expected = collect_scene_result(package['input_document'], package['result'])
    require(package == expected, 'creative_method_package_changed', 409)
    return expected


def scene_context(package):
    return validate_scene_package(package)['input_document']['scene']


def scene_source(package):
    return validate_scene_package(package)['result']['source']


def scene_files(package):
    return {name: content.encode() for name, content in scene_source(package)['files'].items()}


# Explicit names for clients that use production terminology.
build_production_task = prepare_core_task
build_production_input = build_scene_input
validate_production_input = validate_scene_input
collect_production_result = collect_scene_result
validate_production_package = validate_scene_package
