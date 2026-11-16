"""Selection safeguards; synthetic media never stand in for artistic evidence."""
from copy import deepcopy
from uuid import uuid4

import pytest

from video_workbench.animation_quality_policy import select_review
from test_animation_method_service import CRITERIA, work, start, finish


def decision(*, status='passed', outcome='improved', baseline=True, preferences=(),
             criteria=None, observation=None):
    standards = deepcopy(criteria or CRITERIA)
    opinion = dict(preferred_candidate_id='new', outcome=outcome,
        criteria_results=[dict(criterion_id=row['id'], status=status) for row in standards])
    return select_review(criteria=standards, opinion=opinion,
        comparison=dict(candidate_id='new', baseline_candidate_id='old' if baseline else None),
        candidates=[dict(id=identity, technical=dict(status='passed')) for identity in ('old', 'new')],
        current_best_id='old' if baseline else 'new', developer_preferences=preferences,
        observation=observation)


def preference(identity='feedback-1', *, choice='left', left='old', right='new', withdrawn=False):
    return dict(id=identity, kind='preference', left_candidate_id=left,
                right_candidate_id=right, preference=choice, withdrawn=withdrawn)


@pytest.mark.parametrize('status', ['failed', 'insufficient_evidence'])
def test_unqualified_initial_sample_is_retained_without_claiming_goal(status):
    result = decision(status=status, outcome='goal_met', baseline=False)
    assert result['selected_candidate_id'] == 'new'
    assert result['outcome'] == 'inconclusive'
    assert 'initial_sample_retained_without_quality_acceptance' in result['reason_codes']
    assert result['failed_criterion_ids'] == (['causality'] if status == 'failed' else [])


def test_missing_motion_evidence_does_not_erase_a_supported_static_improvement():
    result = decision(status='insufficient_evidence')
    assert result['selected_candidate_id'] == 'new'
    assert result['outcome'] == 'improved'
    assert result['failed_criterion_ids'] == []
    assert result['insufficient_criterion_ids'] == ['causality']


def test_unobserved_motion_or_audio_cannot_establish_the_goal():
    criteria = [dict(CRITERIA[0], requires_motion=True, requires_audio=True)]
    result = decision(outcome='goal_met', criteria=criteria)
    assert result['outcome'] == 'inconclusive'
    assert result['unobserved_criterion_ids'] == ['causality']


def test_latest_active_exact_pair_preference_vetoes_a_contrary_promotion():
    rows = [preference('older', choice='right'), preference('latest'),
            preference('withdrawn', choice='right', withdrawn=True),
            preference('unrelated', left='other')]
    result = decision(outcome='goal_met', preferences=rows)
    assert result['selected_candidate_id'] == 'old'
    assert result['outcome'] == 'regressed'
    assert result['developer_feedback_ids'] == ['latest']
    assert result['model_preferred_candidate_id'] == 'new'
    assert result['model_outcome'] == 'goal_met'


@pytest.mark.parametrize('choice,expected', [('equal', 'equal'), ('neither', 'inconclusive')])
def test_no_developer_preference_for_new_sample_preserves_comparison(choice, expected):
    result = decision(outcome='goal_met', preferences=[preference(choice=choice)])
    assert result['selected_candidate_id'] == 'old'
    assert result['outcome'] == expected


def test_developer_preference_does_not_waive_failed_standards():
    result = decision(status='failed', preferences=[preference(choice='right')])
    assert result['selected_candidate_id'] == 'old'
    assert result['outcome'] == 'inconclusive'
    assert result['failed_criterion_ids'] == ['causality']


def test_withdrawn_or_unrelated_feedback_does_not_block_normal_selection():
    result = decision(outcome='goal_met', preferences=[preference(withdrawn=True), preference(left='other')])
    assert result['selected_candidate_id'] == 'new'
    assert result['outcome'] == 'goal_met'
    assert result['developer_feedback_ids'] == []


def test_failed_frozen_criterion_blocks_new_best_but_preserves_original_review(work, monkeypatch):
    service, _, reviewer, _ = work
    first = finish(service, start(service)['id'])
    original_collect = reviewer.collect

    def collect(*args):
        result = original_collect(*args)
        result['review'].update(outcome='improved', remaining_issues=['UNIT TEST failed standard'])
        result['review']['criteria_results'][0]['status'] = 'failed'
        return result

    monkeypatch.setattr(reviewer, 'collect', collect)
    completed = finish(service, start(service, base_commission_id=first['id'])['id'])
    run = service.commissions.get('developer', completed['id'])
    review = run['reviews'][-1]
    operation = next(row for row in run['operations'] if row['id'] == review['operation_id'])
    assert run['best_candidate_id'] == first['best_candidate_id']
    assert review['outcome'] == 'inconclusive'
    assert completed['stop_reason'] == 'budget_exhausted'
    assert review['criteria_results'][0]['status'] == 'failed'
    assert operation['result']['review']['outcome'] == 'improved'
    assert operation['result']['review']['preferred_candidate_id'] == review['candidate_id']
    selection = review['evidence']['selection_policy']
    assert selection['selected_candidate_id'] == first['best_candidate_id']
    assert selection['failed_criterion_ids'] == ['causality']
    recorded = [row for row in completed['validations']
                if row['evidence'].get('review_operation_id') == operation['id']]
    assert recorded and all(row['outcome'] == 'failed' for row in recorded)
    assert service._record_review('developer', run, operation) == review


def test_all_observed_standards_passed_still_allow_new_best_and_replay(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    completed = finish(service, start(service, base_commission_id=first['id'])['id'])
    run = service.commissions.get('developer', completed['id'])
    review = run['reviews'][-1]
    assert run['best_candidate_id'] != first['best_candidate_id']
    assert review['outcome'] == 'goal_met'
    assert review['evidence']['selection_policy']['failed_criterion_ids'] == []
    operation = next(row for row in run['operations'] if row['id'] == review['operation_id'])
    assert service._record_review('developer', run, operation) == review


def test_motion_uncertainty_remains_incomplete_at_real_store_boundary(work):
    service, _, reviewer, _ = work
    reviewer.incomplete = True
    completed = finish(service, start(service, criteria=[dict(CRITERIA[0], requires_motion=True)])['id'])
    run = service.commissions.get('developer', completed['id'])
    review = run['reviews'][-1]
    selection = review['evidence']['selection_policy']
    assert review['outcome'] == 'inconclusive'
    assert selection['failed_criterion_ids'] == []
    assert selection['insufficient_criterion_ids'] == ['causality']
    assert selection['unobserved_criterion_ids'] == ['causality']
    assert completed['validations'][0]['outcome'] == 'incomplete'
    assert completed['stop_reason'] == 'budget_exhausted'


def test_saved_preference_only_affects_later_frozen_review_and_replay(work):
    service, _, _, _ = work
    first = finish(service, start(service)['id'])
    queued = start(service, base_commission_id=first['id'])
    for _ in range(40):
        service.advance('developer', queued['id'])
        run = service.commissions.get('developer', queued['id'])
        if run['candidates']:
            break
    assert run['candidates'] and not any(row['stage'] == 'review' for row in run['operations'])
    baseline, current = first['best_candidate_id'], run['candidates'][-1]['id']
    before_best = run['best_candidate_id']
    saved = service.feedback('developer', run['id'], dict(request_id=str(uuid4()), kind='preference',
        left_candidate_id=baseline, right_candidate_id=current, preference='left',
        comment='UNIT TEST explicit comparison; saving is not an acceptance action.'))
    assert service.commissions.get('developer', run['id'])['best_candidate_id'] == before_best
    completed = finish(service, queued['id'])
    run = service.commissions.get('developer', completed['id'])
    review = run['reviews'][-1]
    operation = next(row for row in run['operations'] if row['id'] == review['operation_id'])
    assert review['best_candidate_id'] == baseline
    assert review['evidence']['selection_policy']['developer_feedback_ids'] == [saved['id']]
    frozen = deepcopy(operation['input'])
    service.withdraw_feedback('developer', run['id'], dict(request_id=str(uuid4()), feedback_id=saved['id']))
    assert operation['input'] == frozen
    assert service._record_review('developer', run, operation) == review
