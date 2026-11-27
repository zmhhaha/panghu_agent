"""Versioned static-image opinions, separate from trusted technical acceptance.

Only new explicitly bound consumers use this contract. Historical string issues
are never converted, reclassified, or silently treated as optional advice.
"""
from copy import deepcopy
import math
import re

from .contracts import ServiceError


POLICY_SCHEMA = 'video.picture-review-policy.v1'
MAX_ITEMS = 32
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')


def _require(value, code='visual_review_findings_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _text(value, maximum=4000):
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum)


def _identity(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)


def validate_review_policy(value):
    _require(type(value) is dict and set(value) == {'schema', 'criteria', 'evidence_scope'}
        and value['schema'] == POLICY_SCHEMA and value['evidence_scope'] == 'static_only',
        'visual_review_policy_invalid')
    criteria = value['criteria']
    _require(type(criteria) is list and 1 <= len(criteria) <= MAX_ITEMS, 'visual_review_policy_invalid')
    seen = set()
    for row in criteria:
        _require(type(row) is dict and set(row) == {'id', 'requirement', 'text'}, 'visual_review_policy_invalid')
        _identity(row['id'])
        _require(row['id'] not in seen and type(row['requirement']) is str
            and row['requirement'] in {'required', 'optional'}, 'visual_review_policy_invalid')
        _text(row['text'])
        seen.add(row['id'])
    _require(any(row['requirement'] == 'required' for row in criteria), 'visual_review_required_criteria_missing')
    return deepcopy(value)


def _frame_index(frames):
    _require(type(frames) is list and bool(frames), 'visual_review_frames_invalid')
    available = {}
    for frame in frames:
        _require(type(frame) is dict, 'visual_review_frames_invalid')
        identity = frame.get('frame_id', frame.get('file_name'))
        _identity(identity)
        _require(identity not in available and type(frame.get('purpose')) is str
            and frame['purpose'] in {'reference', 'output', 'baseline'},
            'visual_review_frames_invalid')
        available[identity] = frame['purpose']
    _require('output' in available.values(), 'visual_review_output_frame_required')
    return available


def _evidence(value, available):
    _require(type(value) is list and 1 <= len(value) <= len(available), 'visual_review_evidence_invalid')
    for identity in value:
        _identity(identity)
    _require(len(set(value)) == len(value) and set(value) <= set(available)
        and any(available[identity] == 'output' for identity in value), 'visual_review_evidence_invalid')


def validate_classified_review(value, policy, frames):
    policy = validate_review_policy(policy)
    available = _frame_index(frames)
    fields = {'score', 'summary', 'assessments', 'findings', 'limitations', 'stop_recommended'}
    _require(type(value) is dict and set(value) == fields)
    _require(type(value['score']) in (int, float) and 0 <= value['score'] <= 100
        and math.isfinite(value['score']) and type(value['stop_recommended']) is bool)
    _text(value['summary'], 8000)
    for key in ('assessments', 'findings', 'limitations'):
        _require(type(value[key]) is list and len(value[key]) <= MAX_ITEMS)
    for limitation in value['limitations']:
        _text(limitation)
    criteria = {row['id']: row for row in policy['criteria']}
    required = {identity for identity, row in criteria.items() if row['requirement'] == 'required'}
    assessments = {}
    for row in value['assessments']:
        _require(type(row) is dict and set(row) == {'criterion_id', 'status', 'evidence_frame_ids', 'reason'})
        identity = row['criterion_id']
        _identity(identity)
        _require(identity in required and identity not in assessments
            and type(row['status']) is str and row['status'] in {'satisfied', 'violation', 'unresolved'},
            'visual_review_assessment_invalid')
        _text(row['reason'])
        _evidence(row['evidence_frame_ids'], available)
        assessments[identity] = row
    _require(set(assessments) == required, 'visual_review_required_assessment_missing')
    seen = set()
    findings = {identity: [] for identity in required}
    for row in value['findings']:
        fields = {'id', 'criterion_id', 'severity', 'certainty', 'evidence_frame_ids',
                  'observation', 'recommendation'}
        _require(type(row) is dict and set(row) == fields)
        _identity(row['id'])
        _identity(row['criterion_id'])
        _require(row['id'] not in seen and row['criterion_id'] in criteria
            and type(row['severity']) is str and row['severity'] in {'blocking', 'advisory'}
            and type(row['certainty']) is str and row['certainty'] in {'observed', 'uncertain'})
        seen.add(row['id'])
        _text(row['observation'])
        _text(row['recommendation'])
        _evidence(row['evidence_frame_ids'], available)
        identity = row['criterion_id']
        if identity in required:
            status = assessments[identity]['status']
            _require(row['severity'] == 'blocking' and status != 'satisfied'
                and row['certainty'] == {'violation': 'observed', 'unresolved': 'uncertain'}.get(status),
                'visual_review_required_finding_invalid')
            findings[identity].append(row)
        else:
            _require(row['severity'] == 'advisory', 'visual_review_optional_finding_invalid')
    for identity, row in assessments.items():
        _require(row['status'] == 'satisfied' or bool(findings[identity]),
            'visual_review_blocking_finding_missing')
    return deepcopy(value)


def quality_gate(review, policy, frames, minimum_score):
    """Recompute image-opinion gates; this never claims technical/human approval."""
    value = validate_classified_review(review, policy, frames)
    _require(type(minimum_score) in (int, float) and 0 <= minimum_score <= 100
        and math.isfinite(minimum_score), 'visual_review_threshold_invalid')
    blocking = [row['id'] for row in value['findings'] if row['severity'] == 'blocking']
    required_failures = [row['criterion_id'] for row in value['assessments'] if row['status'] != 'satisfied']
    reasons = []
    if value['score'] < minimum_score:
        reasons.append('score_below_minimum')
    if required_failures:
        reasons.append('required_criteria_not_satisfied')
    if blocking:
        reasons.append('blocking_findings')
    if value['stop_recommended']:
        reasons.append('review_recommends_stop')
    return dict(passed=not reasons, reasons=reasons, blocking_finding_ids=blocking,
        advisory_finding_ids=[row['id'] for row in value['findings'] if row['severity'] == 'advisory'],
        unsatisfied_criterion_ids=required_failures, evidence_scope='static_only',
        technical_acceptance=False, user_acceptance=False)
