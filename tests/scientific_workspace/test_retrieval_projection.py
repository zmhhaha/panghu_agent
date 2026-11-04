"""SCI-015: late successful searches must be visible without inventing coverage."""
from copy import deepcopy

from scientific_agent.source_quality import retrieval_summary
from scientific_agent.retrieval_projection import query_outcomes


DEEPSEEK = 'DeepSeek-Harness OR DSH OR "DeepSeek Harness"'
AUTOGEN = '"AutoGen" AND ("multi-agent" OR "agent framework" OR "AgentChat")'


def batch(name, rows):
    return dict(batch_id=name, call_budget=3, papers=[], searches=[dict(source='arxiv',
        query=query, result_count=count, status=status) for query, count, status in rows])


def test_actual_feedback_links_late_phrase_results_and_preserves_empty_history():
    batches = [batch('first', [(DEEPSEEK, 0, 'success'), (AUTOGEN, 0, 'success'), ('unrelated architecture', 1, 'success')]),
               batch('second', [('DeepSeek Harness', 3, 'success'), ('AutoGen', 4, 'success'), ('LangGraph', 2, 'success')])]
    project = dict(plan=dict(content=dict(search_queries=[DEEPSEEK, AUTOGEN]),
        source_policy=dict(max_search_calls=3)), search_batches=batches, selections=[])
    original = deepcopy(project)
    result = retrieval_summary(project)
    assert result['calls_used'] == 3 and result['total_calls_used'] == 6
    assert result['batch_count'] == 2
    assert result['topics_without_evidence'] == []
    assert [row['status'] for row in result['query_outcomes']] == ['relaxed_result', 'relaxed_result']
    deepseek, autogen = result['query_outcomes']
    assert deepseek['planned_query'] == DEEPSEEK
    assert any(row == dict(query='DeepSeek Harness', provider='arxiv', result_count=3,
                          batch_id='second', match='named_entity') for row in deepseek['matched_searches'])
    assert autogen['matched_searches'] == [dict(query='AutoGen', provider='arxiv', result_count=4,
                                               batch_id='second', match='named_entity')]
    assert autogen['unmatched_entities'] == []
    assert result['batches'][0]['providers'][0]['result_count'] == 0
    assert project == original


def test_complete_compound_name_is_never_satisfied_by_a_single_word():
    assert query_outcomes([DEEPSEEK], [batch('b', [('DeepSeek', 3, 'success')])])[0]['status'] == 'no_recorded_result'
    assert query_outcomes([DEEPSEEK], [batch('b', [('Harness', 3, 'success')])])[0]['status'] == 'no_recorded_result'


def test_every_named_project_in_and_query_remains_required():
    query = 'DSH AND AutoGen AND LangGraph AND CrewAI architecture comparison'
    result = query_outcomes([query], [batch('b', [('DSH', 1, 'success'), ('AutoGen', 4, 'success'),
                                                ('LangGraph', 2, 'success')])])[0]
    assert result['status'] == 'partial_relaxed_result'
    assert result['unmatched_entities'] == ['CrewAI']
    assert query_outcomes([query], [batch('b', [('DSH', 1, 'success'), ('AutoGen', 4, 'success'),
        ('LangGraph', 2, 'success'), ('CrewAI', 3, 'success')])])[0]['status'] == 'relaxed_result'


def test_partial_projects_keep_legacy_gap_and_do_not_merge_into_one_topic():
    query = 'DSH AND AutoGen AND LangGraph'
    project = dict(plan=dict(content=dict(search_queries=[query])), selections=[],
                   search_batches=[batch('b', [('AutoGen', 4, 'success')])])
    result = retrieval_summary(project)
    assert result['topics_without_evidence'] == [query]
    assert result['query_outcomes'][0]['unmatched_entities'] == ['DSH', 'LangGraph']


def test_named_or_alternatives_are_choices_while_generic_or_is_only_a_qualifier():
    result = query_outcomes(['DSH AND (AutoGen OR LangGraph)'],
        [batch('b', [('AutoGen', 4, 'success')])])[0]
    assert result['status'] == 'partial_relaxed_result' and result['unmatched_entities'] == ['DSH']
    assert query_outcomes(['DSH AND (AutoGen OR LangGraph)'],
        [batch('b', [('DSH', 1, 'success'), ('LangGraph', 2, 'success')])])[0]['status'] == 'relaxed_result'


def test_successful_or_search_does_not_fill_every_required_project():
    result = query_outcomes(['AutoGen AND LangGraph'],
        [batch('b', [('AutoGen OR LangGraph', 5, 'success')])])[0]
    assert result['status'] == 'partial_relaxed_result'
    assert result['unmatched_entities'] == ['AutoGen', 'LangGraph']


def test_only_positive_successful_rows_have_associations_and_boundaries_are_exact():
    result = query_outcomes(['AutoGen AND architecture'], [batch('b', [
        ('AutoGen', 0, 'success'), ('AutoGen', 8, 'error'), ('AutoGeneral', 5, 'success')])])[0]
    assert result['status'] == 'no_recorded_result' and result['matched_searches'] == []
    assert query_outcomes(['AgentChat'], [batch('b', [('AutoGen', 4, 'success')])])[0]['status'] == 'no_recorded_result'


def test_exact_recorded_query_is_distinct_from_relaxed_association():
    result = query_outcomes([AUTOGEN], [batch('first', [(AUTOGEN, 2, 'partial')])])[0]
    assert result['status'] == 'exact_result'
    assert result['matched_searches'][0]['match'] == 'exact'
    assert result['unmatched_entities'] == []


def test_unknown_boolean_and_negation_stay_conservative():
    for query in ('AutoGen NOT LangGraph', 'AutoGen AND (LangGraph', 'AutoGen AND "LangGraph',
                  'AutoGen AND', 'AutoGen OR OR LangGraph'):
        result = query_outcomes([query], [batch('b', [('AutoGen', 4, 'success')])])[0]
        assert result['status'] == 'no_recorded_result'


def test_untrusted_query_depth_is_bounded_without_changing_raw_records():
    query = '(' * 40 + 'AutoGen' + ')' * 40
    result = query_outcomes([query], [batch('b', [('AutoGen', 4, 'success')])])[0]
    assert result['status'] == 'no_recorded_result'
    assert result['planned_query'] == query
