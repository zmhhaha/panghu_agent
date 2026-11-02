"""Bounded project explanation data, independent of rendering or TTS engines.

Source excerpts establish provenance of excerpts only. They cannot establish
the factual correctness of rewritten narration; adoption needs human review.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from ..contracts import ServiceError, canonical_bytes


INPUT_MAX_BYTES = 1024 * 1024
OUTPUT_MAX_BYTES = 1024 * 1024
FACT_REVIEW_WARNING = '原文摘录仅用于追溯来源，不能证明全部改写准确；采用前请核对事实、数字、术语与因果关系。'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_MARKUP = re.compile(r'<\s*/?\s*(?:script|iframe|svg|html|speak|break|prosody|phoneme|voice|audio|emphasis)\b', re.I)


def _require(value, code='invalid_explanation_contract'):
    if not value:
        raise ServiceError(code, 422)


def _fields(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _text(value, maximum, *, empty=False, prose=False):
    _require(type(value) is str and len(value) <= maximum and (empty or bool(value.strip()))
             and not any((ord(c) < 32 and c not in '\t\r\n') or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    if prose:
        _require(not _MARKUP.search(value) and '```' not in value,
                 'explanation_executable_content_forbidden')


def _number(value, low, high):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    _require(valid)


def _pronunciation(value, narration=None, *, prose=False):
    _require(type(value) is dict and len(value) <= 32)
    for key, reading in value.items():
        _text(key, 80, prose=prose)
        _text(reading, 200, prose=prose)
        _require(narration is None or key in narration, 'explanation_pronunciation_not_in_narration')


def _strings(value, low, high, maximum, *, prose=False):
    _require(type(value) is list and low <= len(value) <= high)
    for item in value:
        _text(item, maximum, prose=prose)


def validate_explanation_input(document):
    _fields(document, {'schema_version', 'project', 'brief', 'scenes', 'instruction'})
    _require(document['schema_version'] == 'video-explanation-input.v1')
    project, brief = document['project'], document['brief']
    _fields(project, {'title', 'script', 'style', 'voice'})
    for key, maximum, empty in [('title', 300, False), ('script', 50000, False),
                                ('style', 4000, True), ('voice', 200, False)]:
        _text(project[key], maximum, empty=empty)
    _fields(brief, {'audience', 'goal', 'tone'})
    _text(brief['audience'], 300, empty=True)
    _text(brief['goal'], 1200, empty=True)
    _require(type(brief['tone']) is str and brief['tone'] in {'conversational', 'precise', 'storytelling'})
    _text(document['instruction'], 2000, empty=True)
    scenes = document['scenes']
    _require(type(scenes) is list and 1 <= len(scenes) <= 24)
    identities = set()
    for scene in scenes:
        _fields(scene, {'scene_id', 'title', 'narration', 'visual_description',
                        'duration_seconds', 'format', 'speech'})
        _text(scene['scene_id'], 100)
        _require(scene['scene_id'] not in identities)
        identities.add(scene['scene_id'])
        for key, maximum in [('title', 300), ('narration', 10000), ('visual_description', 10000)]:
            _text(scene[key], maximum)
        _number(scene['duration_seconds'], 1, 120)
        _require(type(scene['format']) is str and scene['format'] in {'svg', 'html'})
        _fields(scene['speech'], {'speed', 'pronunciation'})
        _number(scene['speech']['speed'], 0.5, 2)
        _pronunciation(scene['speech']['pronunciation'])
    _require(len(canonical_bytes(document)) <= INPUT_MAX_BYTES, 'explanation_input_too_large')
    return deepcopy(document)


def validate_explanation_result(result, input_sha256, input_document):
    document = validate_explanation_input(input_document)
    _require(type(input_sha256) is str and _SHA.fullmatch(input_sha256))
    # The caller must supply the same immutable document used at admission.
    import hashlib
    _require(hashlib.sha256(canonical_bytes(document)).hexdigest() == input_sha256,
             'explanation_input_binding_mismatch')
    _fields(result, {'schema_version', 'input_sha256', 'summary', 'scenes', 'warnings'})
    _require(result['schema_version'] == 'video-explanation-result.v1'
             and result['input_sha256'] == input_sha256, 'explanation_input_binding_mismatch')
    _text(result['summary'], 4000, prose=True)
    scenes = result['scenes']
    _require(type(scenes) is list and len(scenes) == len(document['scenes']))
    for proposed, original in zip(scenes, document['scenes']):
        _fields(proposed, {'scene_id', 'title', 'narration', 'visual_description', 'duration_seconds',
                          'pronunciation', 'key_points', 'visual_strategy', 'visual_steps',
                          'delivery_note', 'source_quotes'})
        _require(proposed['scene_id'] == original['scene_id'], 'explanation_scene_order_mismatch')
        for key, maximum in [('title', 160), ('narration', 10000), ('visual_description', 8000),
                             ('delivery_note', 400)]:
            _text(proposed[key], maximum, empty=key == 'delivery_note', prose=True)
        _number(proposed['duration_seconds'], 1, 120)
        _pronunciation(proposed['pronunciation'], proposed['narration'], prose=True)
        # Match the existing speech admission boundary before adoption. Rules
        # expand exactly once, longest first, as in audio_store.narration_document.
        rules, narration = proposed['pronunciation'], proposed['narration']
        spoken = re.sub('|'.join(re.escape(key) for key in sorted(rules, key=len, reverse=True)),
                        lambda match: rules[match.group()], narration) if rules else narration
        _require(len(narration) <= 2000 and len(spoken) <= 2000
                 and len(spoken.encode('utf-8')) <= 16 * 1024,
                 'explanation_narration_requires_split')
        _strings(proposed['key_points'], 1, 4, 120, prose=True)
        _require(type(proposed['visual_strategy']) is str
                 and proposed['visual_strategy'] in {'flow', 'compare', 'focus'})
        _strings(proposed['visual_steps'], 2, 6, 240, prose=True)
        _strings(proposed['source_quotes'], 1, 6, 2000)
        for quote in proposed['source_quotes']:
            _require(quote in document['project']['script'] or quote in original['narration'],
                     'explanation_source_quote_not_found')
    _strings(result['warnings'], 0, 16, 1000, prose=True)
    _require(len(canonical_bytes(result)) <= OUTPUT_MAX_BYTES, 'explanation_output_too_large')
    return deepcopy(result)
