"""Business-side verification cannot trust model-written search/evidence ledgers."""
from copy import deepcopy
import hashlib
import json
import unittest
from uuid import UUID

from scientific_agent.academic_observations import (
    AcademicObservationError, validate_academic_observations)


ATTEMPT = str(UUID(int=11))
REQUEST = 'e' * 64
POLICY = {'providers': ['crossref'], 'max_results': 5}


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def lines(rows):
    return b''.join(raw(row) + b'\n' for row in rows)


def observation(tool, payload, response):
    request = {'tool': tool, 'payload': payload}
    return dict(call_sha256=hashlib.sha256(raw(request)).hexdigest(), observed_at=123.0,
                state='complete', request=request, response=response)


class AcademicObservationTests(unittest.TestCase):
    def setUp(self):
        self.paper = dict(paper_id='controlled-id', title='Original observed metadata',
            doi='10.1234/controlled', abstract='Observed abstract', sources=['crossref'],
            verification={'status': 'unknown', 'method': 'none'})
        self.search = dict(source='crossref', query='controlled query', result_count=1,
            searched_at='2026-01-01T00:00:00Z', status='success', error=None)
        self.response = dict(schema_version='1', searches=[self.search], papers=[self.paper],
                             database_total=None, notes=[])
        self.envelope = dict(schema_version='1', attempt_id=ATTEMPT, request_sha256=REQUEST,
            observations=[observation('academic_search', {'query': self.search['query']}, self.response)])

    def check(self, *, envelope=None, searches=None, papers=None):
        return validate_academic_observations(raw(self.envelope if envelope is None else envelope),
            lines([self.search] if searches is None else searches),
            lines([self.paper] if papers is None else papers),
            attempt_id=ATTEMPT, request_sha256=REQUEST, source_policy=POLICY)

    def test_exact_observed_records_are_accepted_without_claiming_scientific_truth(self):
        result = self.check()
        self.assertEqual(result['papers'], 1)
        self.assertFalse(result['scientific_accuracy_verified'])

    def test_real_zero_result_search_can_support_insufficient_evidence(self):
        self.search['result_count'] = 0
        self.response['papers'] = []
        self.envelope['observations'][0] = observation('academic_search', {'query': self.search['query']}, self.response)
        self.assertEqual(self.check(papers=[])['papers'], 0)

    def test_no_actual_search_is_not_a_zero_result_search(self):
        self.envelope['observations'] = []
        with self.assertRaises(AcademicObservationError):
            self.check(searches=[], papers=[])

    def test_admitted_or_missing_response_is_unresolved_and_rejected(self):
        for state in ('admitted', 'failed', 'running'):
            with self.subTest(state=state):
                value = deepcopy(self.envelope)
                value['observations'][0]['state'] = state
                with self.assertRaises(AcademicObservationError):
                    self.check(envelope=value)

    def test_attempt_request_or_call_hash_tampering_is_rejected(self):
        for field in ('attempt_id', 'request_sha256', 'call_sha256'):
            with self.subTest(field=field):
                value = deepcopy(self.envelope)
                if field == 'call_sha256':
                    value['observations'][0][field] = '0' * 64
                else:
                    value[field] = str(UUID(int=12)) if field == 'attempt_id' else '0' * 64
                with self.assertRaises(AcademicObservationError):
                    self.check(envelope=value)

    def test_changed_or_omitted_search_rows_cannot_replace_host_observations(self):
        for records in ([], [dict(self.search, result_count=200)], [self.search, self.search]):
            with self.subTest(records=records):
                with self.assertRaises(AcademicObservationError):
                    self.check(searches=records)

    def test_papers_cannot_invent_or_merge_metadata_or_duplicate_ids(self):
        for papers in ([], [dict(self.paper, title='Invented')], [self.paper, self.paper],
                       [dict(self.paper, sources=['crossref', 'pubmed'])]):
            with self.subTest(papers=papers):
                with self.assertRaises(AcademicObservationError):
                    self.check(papers=papers)

    def test_unobserved_verification_upgrade_is_rejected(self):
        with self.assertRaises(AcademicObservationError):
            self.check(papers=[dict(self.paper, verification={'status': 'verified', 'method': 'invented'})])

    def test_exact_matching_identifier_verification_can_upgrade_record(self):
        verification = {'status': 'verified', 'method': 'crossref_exact'}
        self.envelope['observations'].append(observation('academic_verify',
            {'kind': 'doi', 'identifier': self.paper['doi']},
            dict(kind='doi', identifier=self.paper['doi'], verification=verification, metadata={})))
        self.assertEqual(self.check(papers=[dict(self.paper, verification=verification)])['tool_calls'], 2)
        with self.assertRaises(AcademicObservationError):
            self.check(papers=[dict(self.paper, doi='10.1234/other', verification=verification)])

    def test_source_policy_is_enforced_even_for_observed_calls(self):
        self.envelope['observations'][0] = observation('academic_search',
            {'query': self.search['query'], 'providers': ['pubmed']}, self.response)
        with self.assertRaises(AcademicObservationError):
            self.check()

    def test_duplicate_protected_call_or_duplicate_json_key_is_rejected(self):
        self.envelope['observations'] *= 2
        with self.assertRaises(AcademicObservationError):
            self.check()
        with self.assertRaises(AcademicObservationError):
            validate_academic_observations(b'{"schema_version":"1","schema_version":"1"}',
                lines([self.search]), lines([self.paper]), attempt_id=ATTEMPT,
                request_sha256=REQUEST, source_policy=POLICY)


if __name__ == '__main__':
    unittest.main()
