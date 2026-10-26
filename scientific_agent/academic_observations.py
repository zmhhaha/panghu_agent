"""Offline binding of research ledgers to protected academic bridge observations.

The host supplies these bytes only after stopping Agent writers. The business
client checks the same bytes through the authoritative artifact manifest. This
establishes what the bridge returned, not the truth of a paper or its findings.
No network access, provider packages or execution-service imports are needed.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import re
from uuid import UUID


MAX_BYTES = 16 * 1024 * 1024
MAX_CALLS = 100
PROVIDERS = frozenset({'crossref', 'pubmed', 'arxiv', 'openalex', 'semantic_scholar'})


class AcademicObservationError(ValueError):
    pass


def _require(condition):
    if not condition:
        raise AcademicObservationError('research_academic_observation_mismatch')


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AcademicObservationError('research_academic_observation_mismatch') from None


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AcademicObservationError('research_academic_observation_mismatch') from None


def _lines(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_BYTES)
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError:
        raise AcademicObservationError('research_academic_observation_mismatch') from None
    _require(len(lines) <= 25000)
    result = [_json(line) for line in lines]
    _require(all(type(item) is dict for item in result))
    return result


def validate_academic_observations(raw, searches_raw, papers_raw, *, attempt_id,
                                   request_sha256, source_policy):
    """Compare exact search rows and measured paper metadata/verification.

    At least one completed real search is required. An actual search returning
    zero records may support an explicitly insufficient-evidence report. An
    admitted but unresolved tool call cannot be silently omitted or retried by
    accepting a model-written ledger. Paper metadata stays byte-equivalent after
    JSON decoding; verification may only use an observed matching DOI/PMID call.
    """
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = _json(raw)
    _require(type(value) is dict and set(value) == {
        'schema_version', 'attempt_id', 'request_sha256', 'observations'})
    try:
        _require(type(attempt_id) is str and str(UUID(attempt_id)) == attempt_id)
    except (ValueError, TypeError, AttributeError):
        raise AcademicObservationError('research_academic_observation_mismatch') from None
    _require(type(request_sha256) is str and re.fullmatch('[0-9a-f]{64}', request_sha256)
        and value['schema_version'] == '1' and value['attempt_id'] == attempt_id
        and value['request_sha256'] == request_sha256)
    _require(type(source_policy) is dict and set(source_policy) == {'providers', 'max_results'}
        and type(source_policy['providers']) is list and source_policy['providers']
        and all(type(p) is str for p in source_policy['providers'])
        and len(source_policy['providers']) == len(set(source_policy['providers']))
        and set(source_policy['providers']) <= PROVIDERS
        and type(source_policy['max_results']) is int and 1 <= source_policy['max_results'] <= 50)
    observations = value['observations']
    _require(type(observations) is list and 1 <= len(observations) <= MAX_CALLS)
    seen, searches, papers, verifications = set(), [], {}, {}
    search_calls = 0
    for row in observations:
        _require(type(row) is dict and set(row) == {
            'call_sha256', 'observed_at', 'state', 'request', 'response'})
        request, response = row['request'], row['response']
        _require(type(request) is dict and set(request) == {'tool', 'payload'}
            and request['tool'] in ('academic_search', 'academic_verify')
            and type(request['payload']) is dict and type(response) is dict
            and row['state'] == 'complete'
            and type(row['observed_at']) in (int, float)
            and math.isfinite(row['observed_at']) and row['observed_at'] > 0)
        identity = hashlib.sha256(_canonical(request)).hexdigest()
        _require(row['call_sha256'] == identity and identity not in seen)
        seen.add(identity)
        payload = request['payload']
        if request['tool'] == 'academic_search':
            _require(set(payload) <= {'query', 'providers', 'limit'} and 'query' in payload
                and type(payload['query']) is str and payload['query'].strip()
                and len(payload['query']) <= 2000)
            providers = payload.get('providers', source_policy['providers'])
            maximum = payload.get('limit', min(10, source_policy['max_results']))
            _require(type(providers) is list and providers and all(type(p) is str for p in providers)
                and len(providers) == len(set(providers))
                and set(providers) <= set(source_policy['providers'])
                and type(maximum) is int and 1 <= maximum <= source_policy['max_results'])
            _require(set(response) == {'schema_version', 'searches', 'papers', 'database_total', 'notes'}
                and response['schema_version'] == '1' and response['database_total'] is None
                and type(response['searches']) is list and type(response['papers']) is list
                and len(response['searches']) == len(providers)
                and len(response['papers']) <= len(providers) * maximum)
            for source, search in zip(providers, response['searches']):
                _require(type(search) is dict and search.get('source') == source
                    and search.get('query') == payload['query'])
                searches.append(search)
            for paper in response['papers']:
                _require(type(paper) is dict and type(paper.get('paper_id')) is str
                    and bool(paper['paper_id']) and type(paper.get('verification')) is dict)
                papers.setdefault(paper['paper_id'], []).append(paper)
            search_calls += 1
        else:
            _require(set(payload) == {'kind', 'identifier'} and payload['kind'] in ('doi', 'pmid')
                and set(response) == {'kind', 'identifier', 'verification', 'metadata'}
                and response['kind'] == payload['kind'] and type(response['identifier']) is str
                and bool(response['identifier']) and type(response['verification']) is dict)
            verifications.setdefault((response['kind'], response['identifier']), []).append(
                response['verification'])
    _require(search_calls >= 1)
    recorded_searches, recorded_papers = _lines(searches_raw), _lines(papers_raw)
    _require(Counter(_canonical(item) for item in recorded_searches)
        == Counter(_canonical(item) for item in searches))
    identifiers = [item.get('paper_id') for item in recorded_papers]
    _require(all(type(identifier) is str for identifier in identifiers)
        and len(identifiers) == len(set(identifiers)) and set(identifiers) == set(papers))
    for paper in recorded_papers:
        metadata = {key: item for key, item in paper.items() if key != 'verification'}
        originals = papers[paper['paper_id']]
        _require(any(_canonical(metadata) == _canonical({
            key: item for key, item in original.items() if key != 'verification'})
            for original in originals))
        allowed = [item['verification'] for item in originals]
        for kind in ('doi', 'pmid'):
            identifier = paper.get(kind)
            if type(identifier) is str:
                allowed.extend(verifications.get((kind, identifier), ()))
        _require(any(_canonical(paper.get('verification')) == _canonical(item) for item in allowed))
    return {'schema_version': '1', 'tool_calls': len(observations), 'search_calls': search_calls,
        'search_records': len(searches), 'papers': len(papers),
        'basis': 'protected_bridge_observations', 'scientific_accuracy_verified': False}
