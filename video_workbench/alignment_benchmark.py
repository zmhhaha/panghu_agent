"""Frozen, independent listening references for an alignment experiment.

Reference timestamps are deliberately empty until a reviewer supplies them.
Neither estimates nor model output can turn an unreviewed baseline into a pass.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from statistics import median

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_motion_contract import validate_beats
from .word_alignment import resolve_word_anchor, validate_word_alignment


DEFAULT_GATES = dict(minimum_references=10, median_error_seconds=.15,
                     maximum_error_seconds=.30, maximum_uncertainty_seconds=.05)
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(condition):
    if not condition:
        raise ServiceError('invalid_alignment_benchmark', 422)


def _number(value, low, high):
    _require(type(value) in (int, float) and low <= value <= high and math.isfinite(value))
    return float(value)


def _gates(value):
    _require(type(value) is dict and set(value) == set(DEFAULT_GATES))
    _require(type(value['minimum_references']) is int and 10 <= value['minimum_references'] <= 100)
    for key in set(DEFAULT_GATES) - {'minimum_references'}:
        _number(value[key], .001, 1)
    _require(value['median_error_seconds'] <= value['maximum_error_seconds'])
    return deepcopy(value)


def prepare_benchmark(beats, audio_sha256, audio_seconds, spans, *, gates=None):
    """Freeze explicit text occurrences and gates before running an aligner."""
    beats = validate_beats(beats)
    _require(type(audio_sha256) is str and _SHA.fullmatch(audio_sha256))
    seconds = _number(audio_seconds, .1, 300)
    criteria = _gates(DEFAULT_GATES if gates is None else gates)
    _require(type(spans) is list and criteria['minimum_references'] <= len(spans) <= 100)
    references, seen = [], set()
    for span in spans:
        _require(type(span) in (tuple, list) and len(span) == 2)
        start, end = span
        _require(type(start) is int and type(end) is int and
                 0 <= start < end <= len(beats['spoken_text']) and
                 all(end <= prior_start or start >= prior_end
                     for prior_start, prior_end in seen))
        text = beats['spoken_text'][start:end]
        _require(text.strip() and any(c.isalnum() for c in text))
        seen.add((start, end))
        references.append(dict(spoken_start=start, spoken_end=end, text=text,
                               reference_start=None, uncertainty_seconds=None,
                               reviewed_by=None))
    return dict(schema='video.alignment-benchmark.v1', beats=deepcopy(beats),
                audio_sha256=audio_sha256, audio_seconds=seconds, gates=criteria,
                references=references)


def evaluate_benchmark(benchmark, alignment):
    """Compare real unit starts against explicit, separately supplied references.

    This cannot certify that a reviewer actually listened; it preserves their
    attribution and uncertainty. Result provenance remains pending human review.
    """
    fields = {'schema', 'beats', 'audio_sha256', 'audio_seconds', 'gates', 'references'}
    _require(type(benchmark) is dict and set(benchmark) == fields and
             benchmark['schema'] == 'video.alignment-benchmark.v1')
    references = benchmark['references']
    _require(type(references) is list and 10 <= len(references) <= 100)
    # Revalidate the frozen scope and thresholds before inspecting references.
    expected = prepare_benchmark(benchmark['beats'], benchmark['audio_sha256'],
        benchmark['audio_seconds'], [[r.get('spoken_start'), r.get('spoken_end')]
                                    if type(r) is dict else [] for r in references],
        gates=benchmark['gates'])
    document = validate_word_alignment(alignment, expected['beats'])
    _require(document['audio_sha256'] == expected['audio_sha256'] and
             document['audio_seconds'] == expected['audio_seconds'])
    rows, errors = [], []
    for row, frozen in zip(references, expected['references']):
        _require(type(row) is dict and set(row) == set(frozen) and
                 all(row[key] == frozen[key] for key in ('spoken_start', 'spoken_end', 'text')))
        reference, uncertainty, reviewer = (row[key] for key in
            ('reference_start', 'uncertainty_seconds', 'reviewed_by'))
        if reference is None:
            _require(uncertainty is None and reviewer is None)
            rows.append(dict(text=row['text'], spoken_start=row['spoken_start'], status='reference_pending'))
            continue
        reference = _number(reference, 0, expected['audio_seconds'])
        uncertainty = _number(uncertainty, 0, expected['gates']['maximum_uncertainty_seconds'])
        _require(type(reviewer) is str and 0 < len(reviewer) <= 120 and reviewer == reviewer.strip()
                 and all(ord(c) >= 32 for c in reviewer))
        try:
            actual = resolve_word_anchor(document, expected['beats'], row['spoken_start'], row['spoken_end'])
        except ServiceError as exc:
            if exc.code != 'word_anchor_unit_boundary_unsupported':
                raise
            rows.append(dict(text=row['text'], spoken_start=row['spoken_start'],
                             status='unit_boundary_unsupported'))
            continue
        error = abs(actual['start'] - reference)
        errors.append(error)
        rows.append(dict(text=row['text'], spoken_start=row['spoken_start'], status='measured',
                         reference_start=reference, actual_start=actual['start'],
                         absolute_error_seconds=error, uncertainty_seconds=uncertainty,
                         reviewed_by=reviewer))
    complete = len(errors) == len(rows)
    passed = complete and median(errors) <= expected['gates']['median_error_seconds'] and \
        max(errors) <= expected['gates']['maximum_error_seconds']
    status = ('reference_pending' if any(r['status'] == 'reference_pending' for r in rows) else
              'unit_boundary_unsupported' if not complete else 'passed' if passed else 'failed')
    return dict(schema='video.alignment-evaluation.v1', benchmark_sha256=digest(benchmark),
                alignment_sha256=digest(document), source=document['source'], status=status,
                measured_references=len(errors), total_references=len(rows),
                median_error_seconds=median(errors) if errors else None,
                maximum_error_seconds=max(errors) if errors else None,
                gates=expected['gates'], references=rows,
                grants_human_timing_approval=False)
