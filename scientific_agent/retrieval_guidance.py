"""Conservative, deterministic query suggestions; never makes provider calls."""
from __future__ import annotations

import re

_STOP = frozenset('and or not all ti abs title abstract architecture framework comparison compare review survey evaluation research paper original agent agents llm tool tools workflow state persistence recovery human intervention'.split())


def relaxed_queries(query, *, limit=3):
    """Retain named entities from a real query, without inventing new topics."""
    if not isinstance(query, str) or not query.strip():
        return []
    cleaned = re.sub(r'\b(?:all|ti|abs|title|abstract):', '', query, flags=re.I)
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9_-]{1,59}', cleaned)
    names = [token for token in tokens if token.lower() not in _STOP and
             (token.isupper() or re.search(r'[a-z][A-Z]', token) or token in {'DeepSeek', 'Harness'})]
    candidates = names or [token for token in tokens if token.lower() not in _STOP][:2]
    result = []
    for token in candidates:
        if token.casefold() != query.strip().casefold() and token.casefold() not in {s.casefold() for s in result}:
            result.append(token)
    return result[:limit] if limit is not None else result


def low_result_notes(query, count, *, remaining=None):
    if type(count) is not int or count >= 3:
        return []
    suggestions = relaxed_queries(query)
    note = ('Low-result search: this is not evidence that the topic has no literature. '
            'Consider the original project/paper name before complex conjunctions.')
    if suggestions:
        note += ' Suggested simpler queries: ' + ' | '.join(suggestions) + '.'
    if remaining is not None:
        note += ' Remaining admitted search calls in THIS task: ' + str(max(0, remaining)) + '.'
    note += ' Any new query must use the same authorized providers and remaining budget; never retry an uncertain call.'
    return [note]


def query_covered(query, successful_queries):
    """Only exact/same named-entity relaxations count as recorded query coverage.

    This is lexical query coverage, not a claim of scientific topic completeness.
    Every entity in a multi-project query must have a recorded successful query.
    """
    normalized = {re.sub(r'\s+', ' ', item.strip()).casefold() for item in successful_queries}
    if re.sub(r'\s+', ' ', query.strip()).casefold() in normalized:
        return True
    relaxed = relaxed_queries(query, limit=None)
    return bool(relaxed) and all(item.casefold() in normalized for item in relaxed)
