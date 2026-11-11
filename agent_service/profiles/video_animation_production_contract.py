"""Pure production contracts shared by the worker and business adapter.

No business store, renderer or network client is imported. A delivered method
is optional knowledge. Source anchors validate an Agent declaration only;
they never prove that a target is visible or that a method improved a video.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import math
import re

from ..contracts import ServiceError, canonical_bytes, digest
from .video_animation_experiment import ASSET_NAMES, _validate_source_output
from .video_motion_contract import validate_beats


INPUT_SCHEMA = 'video.animation-production-input.v1'
RESULT_SCHEMA = 'video.animation-production-result.v1'
INPUT_MAX_BYTES = 2 * 1024 * 1024
OUTPUT_MAX_BYTES = 2 * 1024 * 1024
DELIVERY_SCHEMA = 'video.production-method-delivery.v1'
MAX_DELIVERY_BYTES = 256 * 1024
DECISION_SCOPE = 'agent_self_report_source_anchors_not_observed_application_or_quality'
DELIVERY_POLICY = ('Read each offered method completely, then decide whether to apply, adapt or reject it '
    'for this exact production task. Return one explicit decision and reason per offered '
    'version. A shortlist is optional creative knowledge, not a source template. Do not '
    'copy demonstration source or claim that delivery proves use or quality. When no '
    'method fits, create freely from the task, narration and actual audio clock.')
_CONTENT = frozenset({'title', 'problem', 'guidance', 'applicability', 'limitations',
                      'criteria', 'runtime_requirements'})
_SCENE = frozenset({'scene_id', 'title', 'visual_description', 'narration', 'beats',
                    'alignment', 'audio_sha256', 'style', 'instruction'})
_BOUNDS = frozenset({'width', 'height', 'fps', 'duration_seconds', 'allowed_engines'})
_ALIGN = frozenset({'schema', 'method', 'review_status', 'audio_sha256', 'spoken_sha256',
                    'audio_seconds', 'version', 'segments', 'reviewed_by', 'reviewed_at'})
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_TARGET = re.compile(r'[a-z][a-z0-9_.-]{0,99}\Z')


def require(value, code='animation_production_contract_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def text(value, maximum, *, empty=False):
    require(type(value) is str and len(value) <= maximum and (empty or bool(value.strip()))
        and not any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127
                    or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def checksum(value):
    require(type(value) is str and _SHA.fullmatch(value) is not None)


def number(value, lower, upper):
    require(type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper)
    return value


def validate_clock(alignment, beats):
    """Validate the existing alignment wire shape without importing the app."""
    require(type(alignment) is dict and set(alignment) in (_ALIGN, _ALIGN | {'provenance', 'corrections'}))
    require(alignment['schema'] == 'video.alignment.v1'
        and type(alignment['method']) is str and alignment['method'] in {'estimated', 'forced_alignment', 'manual_anchors'}
        and type(alignment['review_status']) is str and alignment['review_status'] in {'pending', 'verified'})
    checksum(alignment['audio_sha256'])
    require(alignment['spoken_sha256'] == hashlib.sha256(beats['spoken_text'].encode()).hexdigest())
    seconds = number(alignment['audio_seconds'], .1, 60)
    text(alignment['version'], 120)
    if alignment['review_status'] == 'verified':
        text(alignment['reviewed_by'], 120)
        text(alignment['reviewed_at'], 60)
        try:
            require(datetime.fromisoformat(alignment['reviewed_at'].replace('Z', '+00:00')).tzinfo is not None)
        except (ValueError, TypeError):
            require(False)
    else:
        require(alignment['reviewed_by'] is None and alignment['reviewed_at'] is None)
    rows = alignment['segments']
    require(type(rows) is list and len(rows) == len(beats['segments']))
    previous = 0
    for row, beat in zip(rows, beats['segments']):
        require(type(row) is dict and set(row) == {'beat_id', 'start', 'end'} and row['beat_id'] == beat['id'])
        start, end = number(row['start'], 0, seconds), number(row['end'], 0, seconds)
        require(start >= previous and end - start >= .12)
        previous = end
    if 'provenance' in alignment:
        origin = alignment['provenance']
        require(type(origin) is dict and set(origin) == {'method', 'version', 'alignment_sha256'}
            and type(origin['method']) is str and origin['method'] in {'estimated', 'forced_alignment', 'manual_anchors'})
        text(origin['version'], 120)
        if origin['alignment_sha256'] is not None:
            checksum(origin['alignment_sha256'])
        corrections = alignment['corrections']
        require(type(corrections) is list and len(corrections) <= 24)
        current, seen = {row['beat_id']: row for row in rows}, set()
        for correction in corrections:
            require(type(correction) is dict and set(correction) ==
                {'beat_id', 'previous_start', 'previous_end', 'start', 'end', 'reason'})
            bid = correction['beat_id']
            require(type(bid) is str and bid in current and bid not in seen)
            seen.add(bid)
            text(correction['reason'], 240)
            for key in ('previous_start', 'previous_end', 'start', 'end'):
                number(correction[key], 0, seconds)
            require(correction['previous_start'] < correction['previous_end']
                and correction['start'] == current[bid]['start'] and correction['end'] == current[bid]['end']
                and origin['alignment_sha256'] is not None)
    return deepcopy(alignment)


def validate_core_task(document):
    require(type(document) is dict and set(document) == {'scene', 'constraints', 'assets', 'base_source'})
    scene, bounds = document['scene'], document['constraints']
    require(type(scene) is dict and set(scene) == _SCENE)
    for key, maximum in (('scene_id', 100), ('title', 300), ('visual_description', 16000),
                         ('narration', 10000), ('style', 10000), ('instruction', 16000)):
        text(scene[key], maximum, empty=key in {'style', 'instruction'})
    beats = validate_beats(scene['beats'])
    alignment = validate_clock(scene['alignment'], beats)
    checksum(scene['audio_sha256'])
    require(beats['source_text'] == scene['narration']
        and alignment['audio_sha256'] == scene['audio_sha256'], 'animation_production_audio_binding_mismatch')
    require(type(bounds) is dict and set(bounds) == _BOUNDS)
    require(all(type(bounds[key]) is int and bounds[key] == value
                for key, value in (('width', 1280), ('height', 720), ('fps', 24))))
    require(bounds['allowed_engines'] == ['html-svg'], 'animation_production_engine_not_admitted')
    number(bounds['duration_seconds'], .1, 60)
    require(abs(bounds['duration_seconds'] - alignment['audio_seconds']) <= 1e-9,
            'animation_production_audio_duration_mismatch')
    assets = document['assets']
    require(type(assets) is dict and set(assets) <= ASSET_NAMES)
    for value in assets.values():
        checksum(value)
    if document['base_source'] is not None:
        validate_source(document['base_source'], document)
    require(len(canonical_bytes(document)) <= INPUT_MAX_BYTES, 'animation_production_task_too_large', 413)
    return deepcopy(document)


def validate_delivery(snapshot):
    """Worker-side equivalent of the existing authenticated adapter validator.

    The business adapter cross-checks both implementations. Publication checks
    bind exact content and accepted sample identities; they are not a signature
    or a substitute for the authenticated catalogue/read path.
    """
    require(type(snapshot) is dict and set(snapshot) == {'schema', 'recipient_owner', 'consumer_id',
        'task_sha256', 'allowed_engines', 'selection', 'methods', 'read_receipt', 'policy', 'evidence_scope', 'sha256'}
        and snapshot['schema'] == DELIVERY_SCHEMA)
    require(snapshot['sha256'] == digest({key: value for key, value in snapshot.items() if key != 'sha256'})
        and snapshot['policy'] == DELIVERY_POLICY
        and snapshot['evidence_scope'] == 'frozen_input_delivery_not_agent_application')
    require(type(snapshot['methods']) is list and len(snapshot['methods']) <= 3
        and type(snapshot['allowed_engines']) is list and bool(snapshot['allowed_engines'])
        and all(type(engine) is str for engine in snapshot['allowed_engines'])
        and set(snapshot['allowed_engines']) <= {'html-svg', 'canvas', 'three'})
    for row in snapshot['methods']:
        require(type(row) is dict and _CONTENT <= set(row))
        publication = row.get('publication')
        value_sha = digest({key: row[key] for key in _CONTENT})
        require(type(row.get('id')) is str and row.get('version_id', row['id']) == row['id']
            and row.get('content_sha256') == value_sha and row.get('status') == 'published'
            and type(publication) is dict and publication.get('method_version_id') == row['id']
            and publication.get('method_content_sha256') == value_sha
            and bool(publication.get('reference_validation_ids')) and bool(publication.get('transfer_validation_ids')),
            'production_method_not_published', 409)
        require(type(row['runtime_requirements']) is dict
            and type(row['runtime_requirements'].get('engine')) is str
            and row['runtime_requirements']['engine'] in snapshot['allowed_engines'])
    rows, receipt = snapshot['methods'], snapshot['read_receipt']
    require(len({row['id'] for row in rows}) == len(rows))
    if rows:
        require(type(receipt) is dict and receipt.get('consumer_id') == snapshot['consumer_id']
            and receipt.get('owner') == snapshot['recipient_owner']
            and receipt.get('scope') == 'methods_delivered_to_caller_not_model_observation'
            and receipt.get('method_versions') == [dict(version_id=row['id'], content_sha256=row['content_sha256'])
                                                  for row in rows], 'production_method_receipt_mismatch', 409)
    else:
        require(receipt is None)
    require(len(canonical_bytes(snapshot)) <= MAX_DELIVERY_BYTES, 'production_method_delivery_too_large', 413)
    return deepcopy(snapshot)


def validate_input(document):
    require(type(document) is dict and set(document) ==
        {'schema', 'scene', 'constraints', 'method_delivery', 'assets', 'base_source'}
        and document['schema'] == INPUT_SCHEMA)
    core = validate_core_task({key: document[key] for key in ('scene', 'constraints', 'assets', 'base_source')})
    delivery = validate_delivery(document['method_delivery'])
    require(delivery['task_sha256'] == digest(core)
        and delivery['allowed_engines'] == core['constraints']['allowed_engines'],
        'animation_production_method_task_mismatch', 409)
    text(delivery['recipient_owner'], 200)
    text(delivery['consumer_id'], 400)
    require(len(canonical_bytes(document)) <= INPUT_MAX_BYTES, 'animation_production_input_too_large', 413)
    return deepcopy(document)


def validate_method_decision(delivery, decision, source):
    delivery = validate_delivery(delivery)
    require(type(decision) is dict and set(decision) == {'delivery_sha256', 'decisions'}
        and decision['delivery_sha256'] == delivery['sha256'] and type(decision['decisions']) is list,
        'production_method_decision_binding_invalid', 409)
    offered = {row['id']: row['content_sha256'] for row in delivery['methods']}
    require(len(decision['decisions']) == len(offered), 'production_method_decision_incomplete')
    seen = set()
    for row in decision['decisions']:
        require(type(row) is dict and set(row) ==
            {'version_id', 'content_sha256', 'action', 'reason', 'scene_targets'})
        require(type(row['version_id']) is str and row['version_id'] in offered and row['version_id'] not in seen
            and row['content_sha256'] == offered[row['version_id']]
            and type(row['action']) is str and row['action'] in {'apply', 'adapt', 'reject'},
            'production_method_decision_invalid')
        seen.add(row['version_id'])
        text(row['reason'], 8000)
        targets = row['scene_targets']
        require(type(targets) is list and len(targets) <= 32
            and bool(targets) == (row['action'] != 'reject'), 'production_method_scene_targets_required')
        target_ids = set()
        for target in targets:
            require(type(target) is dict and set(target) ==
                {'target_id', 'description', 'source_file', 'source_anchor'})
            identity = target['target_id']
            require(type(identity) is str and _TARGET.fullmatch(identity) and identity not in target_ids)
            target_ids.add(identity)
            text(target['description'], 4000)
            text(target['source_anchor'], 1000)
            require(type(target['source_file']) is str and target['source_file'] in source['files']
                and target['source_anchor'] in source['files'][target['source_file']],
                'production_method_source_anchor_missing')
    require(seen == set(offered), 'production_method_decision_incomplete')
    return deepcopy(decision)


def validate_source(source, input_document):
    require(type(source) is dict and set(source) == {'engine', 'strategy', 'files', 'files_sha256'})
    _validate_source_output({key: source[key] for key in ('engine', 'strategy', 'files')}, input_document)
    require(source['files_sha256'] == {name: hashlib.sha256(value.encode()).hexdigest()
            for name, value in source['files'].items()}, 'animation_production_source_integrity')
    return deepcopy(source)


def validate_result(document, input_sha256, input_document):
    checksum(input_sha256)
    context = validate_input(input_document)
    require(type(document) is dict and set(document) == {'schema', 'input_sha256', 'source', 'method_decision'}
        and document['schema'] == RESULT_SCHEMA and document['input_sha256'] == input_sha256
        and digest(context) == input_sha256, 'animation_production_input_binding_mismatch')
    source = validate_source(document['source'], context)
    validate_method_decision(context['method_delivery'], document['method_decision'], source)
    require(len(canonical_bytes(document)) <= OUTPUT_MAX_BYTES, 'animation_production_output_too_large', 413)
    return deepcopy(document)
