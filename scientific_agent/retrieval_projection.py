"""Read-only links between planned queries and searches actually recorded.

These are lexical query associations, never literature/topic completeness or a
claim that a relaxed query satisfied the original scientific restrictions.
"""
from __future__ import annotations

import re
import unicodedata

_TOKEN = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'|\(|\)|[^\s()]+')
_FIELDS = re.compile(r'\b(?:all|ti|abs|title|abstract):', re.I)
_GENERIC = frozenset(('and or not all ti abs title abstract architecture framework comparison compare '
    'review survey evaluation research paper original agent agents llm tool tools workflow state '
    'persistence recovery human intervention multi system systems').split())


def _exact(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).strip()).casefold()


def _phrase(value):
    value = unicodedata.normalize('NFKC', _FIELDS.sub('', value)).casefold().replace('_', ' ')
    return tuple(re.findall(r'[^\W_]+', value, flags=re.UNICODE))


def _contains(haystack, needle):
    return bool(needle) and any(haystack[index:index + len(needle)] == needle
                               for index in range(len(haystack) - len(needle) + 1))


def _split(tokens, operator):
    result, current, depth = [], [], 0
    for token in tokens:
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        if depth == 0 and token.upper() == operator:
            result.append(current)
            current = []
        else:
            current.append(token)
    return result + [current]


def _unwrap(tokens):
    while tokens and tokens[0] == '(' and tokens[-1] == ')':
        depth = 0
        for index, token in enumerate(tokens):
            depth += (token == '(') - (token == ')')
            if depth == 0:
                break
        if index != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _named(value):
    words = re.findall(r'[A-Za-z][A-Za-z0-9_-]*', _FIELDS.sub('', value))
    return any(word.casefold() not in _GENERIC and
               (word.isupper() or re.search(r'[a-z][A-Z]', word) or word == 'Harness') for word in words)


def _expression(tokens, *, conjunct=False):
    tokens = _unwrap(tokens)
    alternatives = _split(tokens, 'OR')
    if len(alternatives) > 1:
        children = [_expression(part) for part in alternatives]
        # AutoGen AND ("multi-agent" OR "agent framework" OR "AgentChat")
        # records one project anchor plus alternative qualifiers. Do not turn
        # every alternative qualifier into a separate mandatory project query.
        if conjunct and any(child is None for child in children):
            return None
        children = [child for child in children if child is not None]
        return ('any', children) if children else None
    conjunctions = _split(tokens, 'AND')
    if len(conjunctions) > 1:
        children = [_expression(part, conjunct=True) for part in conjunctions]
    else:
        children = [('atom', token.strip('"\'')) for token in tokens
                    if token not in ('(', ')') and _named(token)]
    children = [child for child in children if child is not None]
    return ('all', children) if children else None


def _anchors(query):
    tokens = _TOKEN.findall(query)
    if not tokens or len(tokens) > 256 or tokens[0].upper() in ('AND', 'OR') or tokens[-1].upper() in ('AND', 'OR'):
        return None
    depth = 0
    for index, token in enumerate(tokens):
        depth += (token == '(') - (token == ')')
        if depth < 0 or depth > 32 or token.upper() == 'NOT':
            return None
        if token.upper() in ('AND', 'OR') and (tokens[index - 1].upper() in ('(', 'AND', 'OR')
                or tokens[index + 1].upper() in (')', 'AND', 'OR')):
            return None
    if depth or query.count('"') % 2 or query.count("'") % 2:
        return None
    return _expression(tokens)


def _atoms(node):
    if node is None:
        return []
    if node[0] == 'atom':
        return [node[1]]
    return [value for child in node[1] for value in _atoms(child)]


def _satisfied(node, matched):
    if node[0] == 'atom':
        return _phrase(node[1]) in matched
    values = [_satisfied(child, matched) for child in node[1]]
    return any(values) if node[0] == 'any' else all(values)


def _missing(node, matched):
    if _satisfied(node, matched):
        return []
    if node[0] == 'atom':
        return [node[1]]
    if node[0] == 'any':
        return [' 或 '.join(dict.fromkeys(_atoms(node)))]
    return list(dict.fromkeys(value for child in node[1] for value in _missing(child, matched)))


def query_outcomes(queries, batches):
    """Project positive recorded rows, preserving batch/provider provenance."""
    successful = []
    for number, batch in enumerate(batches, 1):
        batch_id = batch.get('batch_id', batch.get('id', 'legacy-' + str(number)))
        for row in batch.get('searches', []):
            query, count = row.get('query'), row.get('result_count')
            if (isinstance(query, str) and row.get('status') in ('success', 'partial')
                    and type(count) is int and count > 0):
                successful.append(dict(query=query, provider=row.get('source'), result_count=count,
                                       batch_id=batch_id))
    outcomes = []
    for query in queries:
        expression = _anchors(query)
        atoms = _atoms(expression)
        matched, searches = set(), []
        exact_result = False
        for row in successful:
            if _exact(row['query']) == _exact(query):
                exact_result = True
                searches.append(dict(row, match='exact'))
                continue
            present = [atom for atom in atoms if _contains(_phrase(row['query']), _phrase(atom))]
            if not present:
                continue
            searches.append(dict(row, match='named_entity'))
            # A positive OR query does not identify which named alternative
            # produced its results. Link it, but do not fill every AND anchor.
            if not re.search(r'\b(?:OR|NOT)\b', row['query'], re.I):
                matched.update(_phrase(atom) for atom in present)
        status = ('exact_result' if exact_result else
                  'relaxed_result' if expression is not None and _satisfied(expression, matched) else
                  'partial_relaxed_result' if searches else 'no_recorded_result')
        missing = [] if exact_result or expression is None else _missing(expression, matched)
        outcomes.append(dict(planned_query=query, status=status, matched_searches=searches,
                             unmatched_entities=missing))
    return outcomes
