"""Author and date metadata survives adapters, protected bridge and validators."""
import copy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from tools.academic.providers import (search_arxiv, search_crossref, search_openalex,
    search_pubmed, search_semantic_scholar)
from tools.academic.models import deduplicate_papers, make_paper
from pi_worker.research_tools.bridge import normalize_paper
from pi_worker.research_tools.schemas import RECORD_SCHEMAS
from pi_worker.research_tools.validation import _schema_errors
from scientific_agent.pi_citations import _paper, TraceUnavailable
from scientific_agent.academic_observations import validate_academic_observations
from scientific_agent.workflow_contracts import digest


class BibliographicMetadataTests(unittest.TestCase):
    def arxiv_paper(self, count=26):
        feed = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
        feed += '<id>https://arxiv.org/abs/2501.12345v3</id><title>Luo Harness survey</title>'
        feed += '<published>2025-01-02T00:00:00Z</published><updated>2026-10-25T00:00:00Z</updated>'
        feed += '<arxiv:doi>10.1145/3793671</arxiv:doi><arxiv:journal_ref>ACM 2026</arxiv:journal_ref>'
        feed += ''.join('<author><name>'+('Jian Su' if i == 8 else 'Lan-Zhe Guo' if i == 9 else 'Author '+str(i))+'</name></author>' for i in range(count))
        feed += '<summary>Harness survey abstract.</summary></entry></feed>'
        with patch('tools.academic.providers.request_text', return_value=feed):
            return search_arxiv('Harness', 10)[0]

    def test_all_26_arxiv_authors_and_version_semantics_survive_bridge(self):
        paper = normalize_paper(self.arxiv_paper())
        self.assertEqual(len(paper['authors']), 26)
        bibliography = paper['bibliography']
        self.assertEqual(bibliography['author_count'], 26)
        self.assertEqual(bibliography['author_list_status'], 'complete')
        self.assertEqual(bibliography['dates']['first_posted'], '2025-01-02')
        self.assertEqual(bibliography['dates']['version_updated'], '2026-10-25')
        self.assertEqual(bibliography['version'], 'v3')
        self.assertEqual(bibliography['journal_reference'], 'ACM 2026')
        self.assertIsNone(bibliography['dates']['journal_published'])
        self.assertEqual(paper['year'], 2025)
        self.assertEqual(_schema_errors(paper, RECORD_SCHEMAS['papers'], 'paper'), [])
        _paper(paper)

    def test_harnessvln_authors_nine_and_ten_are_preserved(self):
        paper = normalize_paper(self.arxiv_paper(10))
        self.assertEqual(paper['authors'][8:], ['Jian Su', 'Lan-Zhe Guo'])

    def test_crossref_print_online_and_journal_dates_stay_distinct(self):
        record = dict(title=['Harness study'], type='journal-article', DOI='10.1234/harness',
            author=[dict(given='Author', family=str(i)) for i in range(12)],
            **{'published-print': {'date-parts': [[2026, 12]]},
               'published-online': {'date-parts': [[2026, 10, 25]]},
               'published': {'date-parts': [[2026]]}})
        with patch('tools.academic.providers.request_json', return_value={'message': {'items': [record]}}):
            paper = normalize_paper(search_crossref('harness', 1)[0])
        self.assertEqual(len(paper['authors']), 12)
        self.assertEqual(paper['bibliography']['dates']['print_published'], '2026-12')
        self.assertEqual(paper['bibliography']['dates']['online_published'], '2026-10-25')
        self.assertEqual(paper['bibliography']['dates']['journal_published'], '2026')
        _paper(paper)

    def test_malformed_optional_date_is_unknown_and_does_not_discard_valid_paper(self):
        record = dict(title=['Harness study'], type='journal-article',
            **{'published-print': {'date-parts': [[2026, 13, 1]]}})
        with patch('tools.academic.providers.request_json', return_value={'message': {'items': [record]}}):
            paper = normalize_paper(search_crossref('harness', 1)[0])
        self.assertIsNone(paper['bibliography']['dates']['print_published'])
        _paper(paper)

    def test_upstream_author_truncation_is_explicit(self):
        record = dict(display_name='Harness', is_authors_truncated=True,
            authorships=[dict(author=dict(display_name='Name '+str(i))) for i in range(11)])
        with patch('tools.academic.providers.request_json', return_value={'results': [record]}):
            paper = normalize_paper(search_openalex('harness', 1)[0])
        self.assertEqual(len(paper['authors']), 11)
        self.assertEqual(paper['bibliography']['author_list_status'], 'incomplete')
        self.assertIsNone(paper['bibliography']['author_count'])
        _paper(paper)

    def test_semantic_scholar_retains_all_names_without_claiming_total(self):
        record = dict(title='Harness', authors=[dict(name='Name '+str(i)) for i in range(14)])
        with patch('tools.academic.providers.request_json', return_value={'data': [record]}):
            paper = normalize_paper(search_semantic_scholar('harness', 1)[0])
        self.assertEqual(len(paper['authors']), 14)
        self.assertEqual(paper['bibliography']['author_list_status'], 'not_confirmed')
        _paper(paper)

    def test_pubmed_preserves_all_authors_and_upstream_incomplete_flag(self):
        xml = '<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Harness</ArticleTitle><AuthorList CompleteYN="N">'
        xml += ''.join('<Author><ForeName>Name</ForeName><LastName>'+str(i)+'</LastName></Author>' for i in range(13))
        xml += '</AuthorList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
        with patch('tools.academic.providers.request_json', return_value={'esearchresult': {'idlist': ['123']}}), patch('tools.academic.providers.request_text', return_value=xml), patch('tools.academic.providers.time.sleep'):
            paper = normalize_paper(search_pubmed('harness', 1)[0])
        self.assertEqual(len(paper['authors']), 13)
        self.assertEqual(paper['bibliography']['author_list_status'], 'incomplete')
        _paper(paper)

    def test_names_containing_commas_are_not_split(self):
        record = make_paper('fixture', title='Harness', authors_list=['Surname, Given', 'Second Name'])
        self.assertEqual(normalize_paper(record)['authors'], ['Surname, Given', 'Second Name'])

    def test_legacy_paper_is_accepted_without_fabricating_completeness(self):
        record = normalize_paper(dict(title='Harness', authors=['One', 'Two']))
        before = copy.deepcopy(record)
        self.assertNotIn('bibliography', record)
        _paper(record)
        self.assertEqual(record, before)

    def test_invalid_author_counts_and_calendar_dates_are_rejected(self):
        paper = normalize_paper(self.arxiv_paper())
        for change in ('count', 'date'):
            altered = copy.deepcopy(paper)
            if change == 'count':
                altered['bibliography']['author_count'] = 8
            else:
                altered['bibliography']['dates']['version_updated'] = '2026-13-25'
            with self.assertRaises(TraceUnavailable):
                _paper(altered)

    def test_protected_observation_preserves_exact_bibliography(self):
        paper = normalize_paper(self.arxiv_paper())
        stamp = paper['retrieved_at']
        request = dict(tool='academic_search', payload=dict(query='Harness', providers=['arxiv'], limit=10))
        search = dict(source='arxiv', query='Harness', searched_at=stamp, status='success', result_count=1, error=None)
        attempt = str(uuid4())
        envelope = dict(schema_version='1', attempt_id=attempt, request_sha256='f'*64, observations=[
            dict(call_sha256=digest(request), observed_at=123.5, state='complete', request=request,
                response=dict(schema_version='1', searches=[search], papers=[paper], database_total=None, notes=[]))])
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        result = validate_academic_observations(encode(envelope), encode(search)+b'\n', encode(paper)+b'\n',
            attempt_id=attempt, request_sha256='f'*64, source_policy=dict(providers=['arxiv'], max_results=10))
        self.assertEqual(result['papers'], 1)

    def test_dedup_prefers_complete_list_without_combining_names(self):
        complete = self.arxiv_paper(10)
        incomplete = make_paper('fixture', title=complete['title'], doi=complete['doi'], authors_list=['Wrong Name'])
        merged = deduplicate_papers([incomplete, complete])[0]
        self.assertEqual(merged['authors_list'], complete['authors_list'])
        _paper(normalize_paper(merged))
