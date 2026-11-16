"""Exact, bounded character/word timing beside the frozen sentence contract.

This module does not align audio or infer missing timestamps. An adapter supplies
actual backend units; punctuation/whitespace may differ, but every other spoken
character must match in order. A multi-character unit keeps its one real timing
range and cannot be split by interpolation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re
import unicodedata

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_motion_contract import validate_beats


SCHEMA = 'video.word-alignment.v1'
MAX_UNITS = 10_000
MAX_BACKEND_ITEMS = 20_000
MAX_ALIGNMENT_BYTES = 2 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_SOURCE_KEYS = {'kind', 'provider', 'model', 'version'}
_UNIT_KEYS = {'spoken_start', 'spoken_end', 'text', 'start', 'end'}
_DOCUMENT_KEYS = {'schema', 'audio_sha256', 'audio_seconds', 'spoken_sha256',
                  'beats_sha256', 'source', 'review_status', 'reviewed_by',
                  'reviewed_at', 'units'}


def _require(value, code='invalid_word_alignment'):
    if not value:
        raise ServiceError(code, 422)


def _fields(value, keys):
    _require(type(value) is dict and set(value) == keys)


def _text(value, maximum, *, whitespace=False):
    _require(type(value) is str and 0 < len(value) <= maximum)
    for character in value:
        category = unicodedata.category(character)
        _require(category not in {'Cc', 'Cf', 'Cs'} or
                 (whitespace and character in '\t\r\n'))
    _require(whitespace or value == value.strip() and bool(value.strip()))
    return value


def _number(value, low, high):
    # Bound integers before math.isfinite(), which can overflow on huge ints.
    _require(type(value) in (int, float) and low <= value <= high and math.isfinite(value))
    return float(value)


def _ignored(character):
    return character.isspace() or unicodedata.category(character).startswith('P')


def _positions(text):
    return [index for index, character in enumerate(text) if not _ignored(character)]


def _beats(beats):
    result = validate_beats(beats)
    for key in ('source_text', 'spoken_text'):
        _text(result[key], 10_000, whitespace=True)
    return result


def _source(source):
    _fields(source, _SOURCE_KEYS)
    _require(type(source['kind']) is str and
             source['kind'] in {'forced_alignment', 'provider_native'})
    for key in ('provider', 'model', 'version'):
        _text(source[key], 200)
    return deepcopy(source)


def _checksum(value):
    _require(type(value) is str and _SHA.fullmatch(value))
    return value


def _spoken_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_word_alignment(beats, audio_sha256, audio_seconds, items, source):
    """Bind actual backend units to exact occurrences in ``spoken_text``.

    ``items`` is a list of ``{text, start_time, end_time}``. Standalone
    punctuation may be omitted or ignored. No letters, digits, symbols or
    Chinese characters can be skipped, substituted or normalized here.
    """
    beats = _beats(beats)
    checksum = _checksum(audio_sha256)
    seconds = _number(audio_seconds, .1, 300)
    origin = _source(source)
    _require(type(items) is list and 1 <= len(items) <= MAX_BACKEND_ITEMS)
    spoken = beats['spoken_text']
    positions = _positions(spoken)
    _require(positions)
    cursor, previous, units = 0, 0.0, []
    for item in items:
        _fields(item, {'text', 'start_time', 'end_time'})
        raw = _text(item['text'], 10_000, whitespace=True)
        start = _number(item['start_time'], 0, seconds)
        end = _number(item['end_time'], 0, seconds)
        _require(start <= end)
        characters = [character for character in raw if not _ignored(character)]
        if not characters:
            continue
        _require(end > start and start >= previous)
        selected = positions[cursor:cursor + len(characters)]
        _require(len(selected) == len(characters) and
                 ''.join(spoken[index] for index in selected) == ''.join(characters),
                 'word_alignment_text_mismatch')
        first, last = selected[0], selected[-1] + 1
        units.append(dict(spoken_start=first, spoken_end=last,
                          text=spoken[first:last], start=start, end=end))
        _require(len(units) <= MAX_UNITS)
        cursor += len(characters)
        previous = end
    _require(cursor == len(positions), 'word_alignment_text_mismatch')
    document = dict(schema=SCHEMA, audio_sha256=checksum, audio_seconds=seconds,
                    spoken_sha256=_spoken_digest(spoken), beats_sha256=digest(beats),
                    source=origin, review_status='pending', reviewed_by=None,
                    reviewed_at=None, units=units)
    return validate_word_alignment(document, beats)


def validate_word_alignment(document, beats):
    """Validate exact text, mapping, provenance and recording bindings.

    Automatic adapter output is always pending human review. This independent
    contract intentionally does not grant or import a human approval.
    """
    beats = _beats(beats)
    _fields(document, _DOCUMENT_KEYS)
    _require(document['schema'] == SCHEMA)
    _checksum(document['audio_sha256'])
    seconds = _number(document['audio_seconds'], .1, 300)
    spoken = beats['spoken_text']
    _require(document['spoken_sha256'] == _spoken_digest(spoken) and
             document['beats_sha256'] == digest(beats), 'word_alignment_text_mismatch')
    _source(document['source'])
    _require(document['review_status'] == 'pending' and
             document['reviewed_by'] is None and document['reviewed_at'] is None)
    rows = document['units']
    _require(type(rows) is list and 1 <= len(rows) <= MAX_UNITS)
    positions = _positions(spoken)
    cursor, previous = 0, 0.0
    for row in rows:
        _fields(row, _UNIT_KEYS)
        first, last = row['spoken_start'], row['spoken_end']
        _require(type(first) is int and type(last) is int and
                 0 <= first < last <= len(spoken))
        _require(type(row['text']) is str and row['text'] == spoken[first:last],
                 'word_alignment_text_mismatch')
        count = sum(not _ignored(character) for character in row['text'])
        selected = positions[cursor:cursor + count]
        _require(count and len(selected) == count and
                 first == selected[0] and last == selected[-1] + 1,
                 'word_alignment_text_mismatch')
        start = _number(row['start'], 0, seconds)
        end = _number(row['end'], 0, seconds)
        _require(previous <= start < end)
        previous = end
        cursor += count
    _require(cursor == len(positions), 'word_alignment_text_mismatch')
    _require(len(canonical_bytes(document)) <= MAX_ALIGNMENT_BYTES,
             'word_alignment_too_large')
    return deepcopy(document)


def resolve_word_anchor(document, beats, spoken_start, spoken_end):
    """Resolve a text range only at real backend-unit boundaries.

    Surrounding punctuation or whitespace is harmless. Selecting part of a
    multi-character unit is unsupported: the backend did not supply the inner
    timestamp, so this function refuses to manufacture one.
    """
    document = validate_word_alignment(document, beats)
    spoken = beats['spoken_text']
    _require(type(spoken_start) is int and type(spoken_end) is int and
             0 <= spoken_start < spoken_end <= len(spoken), 'invalid_word_anchor')
    positions = [index for index in range(spoken_start, spoken_end) if not _ignored(spoken[index])]
    _require(positions, 'word_anchor_empty')
    selected = [row for row in document['units']
                if row['spoken_start'] < spoken_end and row['spoken_end'] > spoken_start]
    _require(selected and selected[0]['spoken_start'] == positions[0] and
             selected[-1]['spoken_end'] == positions[-1] + 1,
             'word_anchor_unit_boundary_unsupported')
    return dict(spoken_start=spoken_start, spoken_end=spoken_end,
                text=spoken[spoken_start:spoken_end], start=selected[0]['start'],
                end=selected[-1]['end'])


def beat_segments(document, beats):
    """Project real unit boundaries to old sentence rows without relabelling.

    The caller retains ``document.source``. In particular, native provider
    timing must not be represented as a forced-aligner result. A backend unit
    crossing a sentence boundary cannot be divided to fit the old contract.
    """
    document = validate_word_alignment(document, beats)
    result = []
    for beat in beats['segments']:
        anchor = resolve_word_anchor(document, beats, beat['spoken_start'], beat['spoken_end'])
        result.append(dict(beat_id=beat['id'], start=anchor['start'], end=anchor['end']))
    return result
