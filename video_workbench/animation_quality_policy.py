"""Evidence guards for automatic sample selection, independent of drawing tools.

The reviewer still supplies the artistic judgment. These guards prevent a
relative preference from erasing an explicit failed standard or an earlier
developer comparison. They never turn feedback into quality acceptance.
"""
from __future__ import annotations


SELECTION_SCHEMA = 'video.animation-quality-selection.v1'


def select_review(*, criteria, opinion, comparison, candidates, current_best_id=None,
                  developer_preferences=(), observation=None):
    """Return a bounded host decision from one immutable review input/result.

Preferences must come from the frozen review input, never a live feedback
query: a retry of the same operation must produce the same store request.
Failed standards veto automatic promotion. Missing evidence limits claims but
does not by itself establish that the candidate is worse than its baseline.
"""
    current_id = comparison['candidate_id']
    baseline_id = comparison.get('baseline_candidate_id')
    usable = {row['id'] for row in candidates
              if row.get('technical', {}).get('status') == 'passed'}
    baseline_usable = baseline_id in usable and baseline_id != current_id
    statuses = {row['criterion_id']: row['status'] for row in opinion['criteria_results']}
    failed = [row['id'] for row in criteria if statuses.get(row['id']) == 'failed']
    incomplete = [row['id'] for row in criteria
                  if statuses.get(row['id'], 'insufficient_evidence') == 'insufficient_evidence']
    observation = observation or {}
    unobserved = [row['id'] for row in criteria
                  if ((row.get('requires_motion') and not observation.get('motion_observed'))
                      or (row.get('requires_audio') and not observation.get('audio_observed')))]
    proposed = opinion.get('preferred_candidate_id')
    selected = proposed or current_best_id or current_id
    outcome = opinion['outcome']
    reasons = []
    preference = None
    if baseline_usable:
        pair = {current_id, baseline_id}
        preference = next((row for row in reversed(list(developer_preferences))
            if row.get('kind') == 'preference' and not row.get('withdrawn')
            and {row.get('left_candidate_id'), row.get('right_candidate_id')} == pair
            and row.get('preference') in {'left', 'right', 'equal', 'neither'}), None)

    if selected not in usable:
        selected = baseline_id if baseline_usable else current_id
        outcome = 'inconclusive'
        reasons.append('selected_candidate_not_technically_verified')

    if failed:
        reasons.append('frozen_criteria_failed')
        if baseline_usable:
            selected = baseline_id
        if outcome in {'goal_met', 'improved'}:
            outcome = 'inconclusive'

    if preference:
        choice = preference['preference']
        preferred_id = (preference['left_candidate_id'] if choice == 'left' else
                        preference['right_candidate_id'] if choice == 'right' else None)
        # A developer comparison can veto an automatic contrary promotion;
        # choosing a candidate does not waive its standards or certify motion.
        if preferred_id == baseline_id:
            selected = baseline_id
            outcome = 'regressed'
            reasons.append('developer_prefers_compared_baseline')
        elif choice in {'equal', 'neither'}:
            selected = baseline_id
            outcome = 'equal' if choice == 'equal' else 'inconclusive'
            reasons.append('developer_reports_' + choice)
        elif selected == baseline_id and not failed and proposed == baseline_id:
            # Conflicting judgments are unresolved. Do not promote either
            # model judgment to an assertion that the developer was wrong.
            outcome = 'inconclusive'
            reasons.append('developer_and_reviewer_disagree')

    if incomplete:
        reasons.append('criteria_evidence_incomplete')
    if unobserved:
        reasons.append('required_observation_unavailable')
    if outcome == 'goal_met' and (failed or incomplete or unobserved or not criteria
                                  or selected != current_id or current_id not in usable):
        outcome = 'inconclusive'
        reasons.append('goal_not_established')
    if not baseline_usable and (failed or incomplete):
        # Storage retains an initial available sample even when its quality
        # is unresolved; the selected ID is not an acceptance certificate.
        reasons.append('initial_sample_retained_without_quality_acceptance')
    if not reasons:
        reasons.append('review_supported_by_available_evidence')

    return dict(schema=SELECTION_SCHEMA,
        model_preferred_candidate_id=proposed, model_outcome=opinion['outcome'],
        selected_candidate_id=selected, outcome=outcome, reason_codes=reasons,
        failed_criterion_ids=failed, insufficient_criterion_ids=incomplete,
        unobserved_criterion_ids=unobserved,
        developer_feedback_ids=[preference['id']] if preference else [])
