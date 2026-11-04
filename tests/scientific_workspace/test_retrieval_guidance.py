"""SCI-009 bounded low-result guidance and honest lexical coverage checks."""
import pytest

from scientific_agent.retrieval_guidance import (low_result_notes,
    query_covered, relaxed_queries)


def test_two_autogen_results_suggest_recorded_project_name_instead_of_expanding_scope():
    query = 'all:AutoGen AND architecture AND persistence AND human intervention'
    assert relaxed_queries(query) == ['AutoGen']
    notes = low_result_notes(query, 2, remaining=1)
    assert len(notes) == 1
    assert 'Suggested simpler queries: AutoGen.' in notes[0]
    assert 'Remaining admitted search calls in THIS task: 1.' in notes[0]
    assert 'not evidence that the topic has no literature' in notes[0]
    assert 'never retry an uncertain call' in notes[0]


def test_zero_remaining_budget_is_visible_and_suggestions_never_invoke_providers(monkeypatch):
    from tools.academic.search import PROVIDER_SEARCHERS
    def forbidden(*args, **kwargs):
        pytest.fail('Query suggestions may not issue provider requests')
    for name in PROVIDER_SEARCHERS:
        monkeypatch.setitem(PROVIDER_SEARCHERS, name, forbidden)
    notes = low_result_notes('AutoGen architecture', 0, remaining=0)
    assert 'Remaining admitted search calls in THIS task: 0.' in notes[0]
    assert 'same authorized providers and remaining budget' in notes[0]


def test_normal_result_count_does_not_add_spurious_low_result_advice():
    assert low_result_notes('AutoGen architecture', 3, remaining=1) == []
    assert low_result_notes('AutoGen architecture', 10, remaining=0) == []


def test_exact_and_simple_same_entity_queries_can_close_recorded_query_gap():
    query = 'AutoGen AND architecture AND persistence'
    assert query_covered(query, ['  autogen  AND   architecture AND persistence '])
    assert query_covered(query, ['AutoGen'])
    assert not query_covered(query, ['LangGraph'])
    assert not query_covered(query, [])


def test_single_project_cannot_close_multi_project_query_gap():
    query = 'AutoGen AND LangGraph architecture comparison'
    assert relaxed_queries(query) == ['AutoGen', 'LangGraph']
    assert not query_covered(query, ['AutoGen'])
    assert not query_covered(query, ['LangGraph'])
    assert query_covered(query, ['AutoGen', 'LangGraph'])


def test_query_with_more_than_three_projects_requires_all_entities_not_only_display_suggestions():
    query = 'DSH AutoGen LangGraph CrewAI architecture comparison'
    assert not query_covered(query, ['DSH', 'AutoGen', 'LangGraph'])
    assert query_covered(query, ['DSH', 'AutoGen', 'LangGraph', 'CrewAI'])


def test_bridge_keeps_one_provider_request_and_original_record_shape_when_adding_notes(monkeypatch):
    from pi_worker.research_tools.bridge import search
    from tools.academic.search import PROVIDER_SEARCHERS
    calls = []
    def provider(query, limit):
        calls.append((query, limit))
        return [dict(title='AutoGen architecture synthetic source ' + str(index),
                     abstract='Synthetic abstract; not a real scientific result.',
                     authors=['Synthetic Author'], provider='arxiv') for index in range(2)]
    monkeypatch.setitem(PROVIDER_SEARCHERS, 'arxiv', provider)
    query = 'AutoGen architecture AND persistence'
    result = search(query, providers=['arxiv'], limit=2)
    assert calls == [(query, 2)]
    assert set(result) == {'schema_version', 'searches', 'papers', 'database_total', 'notes'}
    assert len(result['searches']) == 1 and len(result['papers']) == 2
    observed = result['searches'][0]
    assert set(observed) == {'source', 'query', 'searched_at', 'status', 'result_count', 'error'}
    assert observed['query'] == query and observed['source'] == 'arxiv'
    assert observed['status'] == 'success' and observed['result_count'] == 2
    assert result['database_total'] is None
    assert any('Suggested simpler queries: AutoGen.' in note for note in result['notes'])
    assert all(paper['full_text_path'] is None and not paper['availability']['full_text']['available']
               for paper in result['papers'])
