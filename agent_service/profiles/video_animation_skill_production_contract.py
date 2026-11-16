"""Production v4: film custody and progressively read, frozen Skill knowledge.

Host evidence is a separate signed handoff. A declared decision/source anchor
does not prove visible application, continuity, aesthetic quality or publication.
Earlier production input/result contracts are deliberately unchanged.
"""
from copy import deepcopy
import re

from ..animation_skills_contract import validate_skill_catalog
from ..contracts import canonical_bytes, digest
from .video_animation_production_contract import (
    INPUT_MAX_BYTES, OUTPUT_MAX_BYTES, HANDOFF_SCHEMA,
    require, checksum, text, validate_core_task, validate_handoff, validate_source,
)


INPUT_SCHEMA = 'video.animation-production-input.v4'
RESULT_SCHEMA = 'video.animation-production-result.v4'
MAX_TOOL_EVIDENCE_BYTES = 2 * 1024 * 1024
DECISION_SCOPE = 'agent_declared_source_anchors_not_observed_application_or_quality'
CORE_FIELDS = frozenset({'scene', 'constraints', 'assets', 'base_source', 'film_context'})
RAW_RESULT_FIELDS = frozenset({'schema', 'input_sha256', 'source', 'skill_decisions', 'handoff'})
_TARGET = re.compile(r'[a-z][a-z0-9_.-]{0,99}\Z')


def validate_input(document):
    require(type(document) is dict and set(document) == CORE_FIELDS | {'schema', 'skill_catalog'}
            and document['schema'] == INPUT_SCHEMA, 'animation_skill_production_input_invalid')
    core = validate_core_task({key: document[key] for key in CORE_FIELDS},
                             engine_aware=True, film_aware=True)
    catalog = validate_skill_catalog(document['skill_catalog'])
    require(catalog['task_sha256'] == digest(core)
            and catalog['allowed_engines'] == core['constraints']['allowed_engines'],
            'animation_skill_production_catalog_binding_mismatch', 409)
    require(len(canonical_bytes(document)) <= INPUT_MAX_BYTES,
            'animation_skill_production_input_too_large', 413)
    return deepcopy(document)


def validate_skill_decisions(decisions, catalog, source, read_package_sha256s):
    """Bind declarations to actual successful root reads, not search hits."""
    catalog = validate_skill_catalog(catalog)
    require(type(read_package_sha256s) in (set, frozenset, list, tuple),
            'animation_skill_production_host_reads_required', 409)
    reads = set()
    for value in read_package_sha256s:
        checksum(value)
        require(value not in reads, 'animation_skill_production_duplicate_host_read')
        reads.add(value)
    offered = {row['package_sha256'] for row in catalog['skills']}
    require(reads <= offered, 'animation_skill_production_unknown_host_read', 409)
    require(type(decisions) is list and len(decisions) == len(reads),
            'animation_skill_production_decisions_incomplete')
    seen = set()
    for row in decisions:
        require(type(row) is dict and set(row) == {'package_sha256', 'action', 'reason', 'scene_targets'},
                'animation_skill_production_decision_invalid')
        checksum(row['package_sha256'])
        require(row['package_sha256'] in reads and row['package_sha256'] not in seen
                and type(row['action']) is str and row['action'] in {'apply', 'adapt', 'reject'},
                'animation_skill_production_decision_invalid')
        seen.add(row['package_sha256'])
        text(row['reason'], 8000)
        targets = row['scene_targets']
        require(type(targets) is list and len(targets) <= 32
                and bool(targets) == (row['action'] != 'reject'),
                'animation_skill_production_scene_targets_required')
        target_ids = set()
        for target in targets:
            require(type(target) is dict and set(target) == {
                'target_id', 'description', 'source_file', 'source_anchor'})
            identity = target['target_id']
            require(type(identity) is str and _TARGET.fullmatch(identity) is not None
                    and identity not in target_ids)
            target_ids.add(identity)
            text(target['description'], 4000)
            text(target['source_anchor'], 1000)
            require(type(target['source_file']) is str and target['source_file'] in source['files']
                    and target['source_anchor'] in source['files'][target['source_file']],
                    'animation_skill_production_source_anchor_missing')
    require(seen == reads, 'animation_skill_production_decisions_incomplete')
    return deepcopy(decisions)


def validate_result(document, input_sha256, input_document, *, read_package_sha256s, tool_evidence_sha256):
    """Validate a host-collected artifact after checking its separate evidence.

    The caller must obtain read_package_sha256s and tool_evidence_sha256 from
    authenticated host evidence; Agent-returned fields cannot supply them.
    """
    context = validate_input(input_document)
    checksum(input_sha256)
    checksum(tool_evidence_sha256)
    require(type(document) is dict and set(document) == RAW_RESULT_FIELDS | {'skill_tool_evidence_sha256'}
            and document['schema'] == RESULT_SCHEMA and document['input_sha256'] == input_sha256
            and digest(context) == input_sha256
            and document['skill_tool_evidence_sha256'] == tool_evidence_sha256,
            'animation_skill_production_result_binding_mismatch', 409)
    source = validate_source(document['source'], context)
    validate_skill_decisions(document['skill_decisions'], context['skill_catalog'], source,
                             read_package_sha256s)
    validate_handoff(document['handoff'], film_sha256=context['film_context']['film_sha256'],
                     scene_id=context['scene']['scene_id'], source=source)
    require(len(canonical_bytes(document)) <= OUTPUT_MAX_BYTES,
            'animation_skill_production_output_too_large', 413)
    return deepcopy(document)
