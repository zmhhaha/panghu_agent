"""Context/evidence guarantees, not evidence that generated art improved."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_collaboration_contract import validate_input
from video_workbench.creative_direction import (
    MAX_DIRECTION_BYTES, MAX_FEEDBACK_RECORDS, SCHEMA, build_creative_direction,
)


def opinion(identity, comment, **extra):
    return dict(id=identity, kind='comment', candidate_id='baseline', comment=comment, **extra)


def test_engine_choice_changes_data_without_inventing_an_art_template_or_assessment():
    values = [build_creative_direction(goal='Original expressive goal', allowed_engines=[engine])
              for engine in ('html-svg', 'canvas', 'three')]
    assert [row['allowed_engines'] for row in values] == [['html-svg'], ['canvas'], ['three']]
    comparable = [{key: value for key, value in row.items() if key != 'allowed_engines'} for row in values]
    assert comparable[0] == comparable[1] == comparable[2]
    assert values[0]['schema'] == SCHEMA
    assert not {'source', 'plan', 'component', 'review', 'criteria_results'} & set(values[0])
    assert len(canonical_bytes(values[0])) <= MAX_DIRECTION_BYTES


def test_exact_user_input_bindings_are_deterministic_and_callers_are_not_modified():
    goal, feedback = '保留要求中的独特风格。', '  不要只让构图清楚，却丢失原来的质感。\n'
    criteria = [dict(id='material', description='Readable material character', requires_motion=False)]
    original = [opinion('f1', feedback, start_seconds=2, end_seconds=3,
                        region=dict(x=.1, y=.2, width=.3, height=.4))]
    before = deepcopy((criteria, original))
    first = build_creative_direction(goal=goal, criteria=criteria, feedback=feedback,
                                     allowed_engines=['canvas'], developer_feedback=original)
    assert first == build_creative_direction(goal=goal, criteria=criteria, feedback=feedback,
                                             allowed_engines=['canvas'], developer_feedback=original)
    assert (criteria, original) == before
    assert first['input_bindings'] == dict(goal_sha256=digest(goal),
        criteria_sha256=digest(criteria), feedback_sha256=digest(feedback))
    record = first['developer_feedback']['records'][0]
    assert record['comment'] == feedback and record['text_status'] == 'verbatim'
    assert record['region'] == original[0]['region']
    record['region']['x'] = .9
    assert original[0]['region']['x'] == .1


def test_large_baseline_source_is_bound_without_entering_each_component_context():
    baseline = dict(id='baseline', media={'sha256': 'a' * 64},
        source=dict(engine='three', strategy=dict(description='An authored intent', tradeoffs='Unobserved'),
                    files={'scene.html': 'PRIVATE ORIGINAL SOURCE ' * 10000}))
    value = build_creative_direction(goal='Keep the observed strengths', baseline=baseline,
                                     allowed_engines=['three'])
    binding = value['baseline']
    assert binding['candidate_id'] == baseline['id'] and binding['engine'] == 'three'
    assert binding['source_sha256'] == digest(baseline['source'])
    assert binding['strategy_sha256'] == digest(baseline['source']['strategy'])
    assert b'PRIVATE ORIGINAL SOURCE' not in canonical_bytes(value)
    assert not {'source', 'media', 'observed_strengths', 'passed'} & set(binding)


def test_feedback_coverage_is_explicit_and_withdrawals_cannot_silently_return():
    rows = [opinion('f' + str(index), 'Original opinion ' + str(index)) for index in range(9)]
    rows.append(opinion('withdrawn', 'This is no longer active', withdrawn=True))
    value = build_creative_direction(goal='New work', developer_feedback=rows)
    feedback = value['developer_feedback']
    assert [row['id'] for row in feedback['records']] == ['f5', 'f6', 'f7', 'f8']
    assert feedback['coverage'] == dict(input_count=10, represented_count=MAX_FEEDBACK_RECORDS,
        included_text_count=4, omitted_text_count=0, withdrawn_count=1, older_count=5,
        unrepresented_size_count=0, input_sha256=digest(rows))
    assert all(row['comment'] == rows[5 + index]['comment']
               for index, row in enumerate(feedback['records']))


def test_oversized_opinion_is_bound_and_reported_without_truncation_or_paraphrase():
    very_large_comment = '不得篡改这条完整的开发者原话。' * 1500
    rows = [opinion('large', very_large_comment), opinion('recent', 'Newest exact opinion')]
    value = build_creative_direction(goal='Expressive target', developer_feedback=rows)
    omitted, included = value['developer_feedback']['records']
    assert omitted['text_status'] == 'omitted_size_limit' and 'comment' not in omitted
    assert omitted['comment_sha256'] == digest(very_large_comment)
    assert omitted['comment_utf8_bytes'] == len(very_large_comment.encode('utf-8'))
    assert included['comment'] == rows[1]['comment'] and included['text_status'] == 'verbatim'
    assert value['developer_feedback']['coverage']['omitted_text_count'] == 1
    assert len(canonical_bytes(value)) <= MAX_DIRECTION_BYTES
    assert rows[0]['comment'] == very_large_comment


def test_maximum_valid_ids_still_produce_bounded_context_with_explicit_record_omissions():
    rows = [dict(id=str(index) * 200, commission_id='r' * 200, kind='preference',
        left_candidate_id='l' * 200, right_candidate_id='n' * 200, preference='left',
        comment='长评论' * 4000) for index in range(4)]
    value = build_creative_direction(goal='Review the complete target', developer_feedback=rows,
        baseline=dict(id='b' * 200, source={'engine': 'canvas', 'strategy': {}}))
    coverage = value['developer_feedback']['coverage']
    assert len(canonical_bytes(value)) <= MAX_DIRECTION_BYTES
    assert coverage['input_count'] == coverage['represented_count'] + coverage['unrepresented_size_count']
    assert coverage['input_sha256'] == digest(rows)


def test_preference_is_preserved_as_an_opinion_with_both_actual_candidate_bindings():
    preference = dict(id='preference', kind='preference', left_candidate_id='old',
        right_candidate_id='new', preference='left', comment='新版本不如原版。')
    value = build_creative_direction(goal='Try again', developer_feedback=[preference])
    record = value['developer_feedback']['records'][0]
    assert all(record[key] == preference[key] for key in preference)
    assert 'opinion' in record['evidence_scope']
    assert not {'observed_strengths', 'accepted', 'criteria_results', 'visual_passed'} & set(record)


def test_guidance_fits_the_existing_collaboration_contract_without_rewriting_v1():
    direction = build_creative_direction(goal='Create an original diagram',
                                         allowed_engines=['html-svg', 'canvas', 'three'])
    document = dict(schema='video.animation-collaboration-input.v1', phase='plan',
        goal='Create an original diagram', criteria=[], constraints=dict(width=1280, height=720,
            fps=24, duration_seconds=8, allowed_engines=['html-svg', 'canvas', 'three'],
            creative_direction=direction), assets={}, reference=None, feedback='', brief=None,
        component_id=None, dependencies=[], methods=[], base_source=None)
    assert validate_input(document)['constraints']['creative_direction'] == direction


@pytest.mark.parametrize('change', [dict(goal=None), dict(allowed_engines=['unknown']),
    dict(allowed_engines=['canvas', 'canvas']), dict(baseline={'source': {}}),
    dict(developer_feedback=[dict(kind='preference', preference='left')]),
    dict(developer_feedback=[opinion('bad', 'Opinion', start_seconds=float('nan'))])])
def test_invalid_context_is_rejected_instead_of_accepted_as_observed_evidence(change):
    with pytest.raises(ServiceError, match='animation_creative_direction_invalid'):
        build_creative_direction(**dict(dict(goal='Valid original goal'), **change))
