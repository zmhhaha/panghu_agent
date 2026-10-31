"""Regression cases reported in SCI-005 and SCI-007; entirely offline."""
import copy
import json
import unittest

from scientific_agent.source_quality import assess_source, retrieval_summary
from scientific_agent.workspace_materials import (MaterialError, configure_material,
    material_display, materials_summary, parse_material)


class SourceQualityTests(unittest.TestCase):
    requirements = dict(question='DSH Harness LLM agent evaluation', time_range='2022—2026')

    def test_reported_crossref_placeholder_samples_are_not_default_inclusions(self):
        for title, year in [('New Test Pub 01', 2017),
                ('Article with BY print pub date, CY online pub date, issue BY print pub date', 2000)]:
            paper = dict(title=title, year=year, doi='10.5555/fixture', abstract=None)
            before = copy.deepcopy(paper)
            result = assess_source(paper, self.requirements)
            self.assertFalse(result['default_included'])
            self.assertTrue({'placeholder_title', 'outside_year_range'} <= {row['code'] for row in result['reasons']})
            self.assertEqual(paper, before)

    def test_doi_prefix_does_not_establish_authenticity(self):
        for doi in ('10.5555/real', '10.1234/real'):
            result = assess_source(dict(title='Harness evaluation for LLM agents', year=2026,
                doi=doi, abstract='Harness experiments.'), self.requirements)
            self.assertTrue(result['default_included'])

    def test_testing_research_is_not_mistaken_for_placeholder(self):
        result = assess_source(dict(title='Test-driven harness evaluation for LLM agents',
            year=2025, abstract='An agent testing method.'), self.requirements)
        self.assertTrue(result['default_included'])

    def test_unrelated_metadata_is_excluded_and_missing_abstract_disclosed(self):
        result = assess_source(dict(title='Cell growth in plants', year=2024), self.requirements)
        self.assertEqual(result['reasons'][0]['code'], 'no_topic_match')
        self.assertEqual(result['warnings'][0]['code'], 'metadata_only')

    def test_cross_language_uncertainty_is_not_fabricated_relevance(self):
        result = assess_source(dict(title='Foundation model agents', year=2025),
            dict(question='通用智能体研究', time_range='2022-2026'))
        self.assertTrue(result['default_included'])
        self.assertIn('cross_language_relevance_unconfirmed', [row['code'] for row in result['warnings']])

    def test_chinese_topic_with_english_query_does_not_exclude_relevant_chinese_title(self):
        result = assess_source(dict(title='人工智能促进教育公平的机制',
            abstract='本研究讨论人工智能在教育中的公平性影响。', year=2025),
            dict(question='人工智能对教育公平的影响', time_range='2022—2026'),
            ['educational equity AI'])
        self.assertTrue(result['default_included'])
        self.assertIn('人工智能', result['matched_terms'])

    def test_opposite_script_without_matches_is_uncertain_not_excluded(self):
        cases = [
            (dict(title='自主计算系统', abstract='讨论环境交互。', year=2025),
             dict(question='Harness evaluation for autonomous agents'), ['agent harness']),
            (dict(title='Foundation model agents', year=2025),
             dict(question='通用智能体研究'), []),
        ]
        for paper, requirements, queries in cases:
            result = assess_source(paper, requirements, queries)
            self.assertTrue(result['default_included'])
            self.assertIn('cross_language_relevance_unconfirmed',
                          [row['code'] for row in result['warnings']])

    def test_credible_same_language_mismatch_is_still_excluded(self):
        result = assess_source(dict(title='Cell growth in plants', year=2025),
            dict(question='Harness evaluation for autonomous agents'))
        self.assertFalse(result['default_included'])
        self.assertIn('no_topic_match', [row['code'] for row in result['reasons']])

    def test_open_and_ambiguous_date_scopes_are_not_misread_as_single_year(self):
        for scope in ('2022年至今', 'since 2022', 'from 2022 onward'):
            result = assess_source(dict(title='Harness evaluation for LLM agents', year=2025),
                dict(self.requirements, time_range=scope))
            self.assertTrue(result['default_included'], scope)
            self.assertEqual(result['year_range'][0], 2022)
        for scope in ('近五年（2022）', '2022 or 2026', '2022年以来，不含2020年的记录'):
            result = assess_source(dict(title='Harness evaluation for LLM agents', year=2025),
                dict(self.requirements, time_range=scope))
            self.assertTrue(result['default_included'], scope)
            self.assertIsNone(result['year_range'])
            self.assertIn('year_range_unconfirmed', [row['code'] for row in result['warnings']])

    def test_explicit_single_year_and_upper_bound_still_filter(self):
        for scope in ('2022', '2022年', 'before 2024', '2024年以前', '截至2022年'):
            result = assess_source(dict(title='Harness evaluation for LLM agents', year=2025),
                dict(self.requirements, time_range=scope))
            self.assertFalse(result['default_included'], scope)
            self.assertIn('outside_year_range', [row['code'] for row in result['reasons']])

    def test_journal_filter_uses_journal_date_instead_of_preprint_first_year(self):
        paper = dict(title='Harness evaluation for LLM agents', year=2025,
            bibliography=dict(source_type='journal', dates=dict(first_posted='2025-06-01',
                version_updated='2026-10-25', journal_published='2026')))
        result = assess_source(paper, dict(self.requirements, time_range='2026'))
        self.assertTrue(result['default_included'])
        self.assertEqual(result['year_basis'], 'journal_published')

    def test_unknown_year_requires_user_review_when_range_is_explicit(self):
        result = assess_source(dict(title='Harness for LLM agents'), self.requirements)
        self.assertFalse(result['default_included'])
        self.assertIn('year_unconfirmed', [row['code'] for row in result['reasons']])

    def test_budget_is_per_retrieval_not_cumulative_project_calls(self):
        def batch(queries):
            return dict(searches=[dict(source='arxiv', query=query, status='success', result_count=0)
                                  for query in queries], papers=[])
        project = dict(plan=dict(source_policy=dict(max_search_calls=3),
            output=dict(search_queries=['harness', 'agent', 'missing'])),
            search_batches=[batch(['old1', 'old2', 'old3']), batch(['harness'])], selections=[])
        result = retrieval_summary(project)
        self.assertEqual(result['calls_used'], 1)
        self.assertEqual(result['total_calls_used'], 4)
        self.assertFalse(result['budget_exhausted'])
        self.assertEqual(result['topics_without_evidence'], ['harness', 'agent', 'missing'])
        self.assertEqual(result['providers'][-1]['query'], 'harness')


class MaterialScopeTests(unittest.TestCase):
    def test_file_read_is_not_a_claim_about_embedded_papers(self):
        material = parse_material('dsh-review-evidence-pack.md', b'Paper A abstract\nOfficial documentation body')
        view = material_display(material)
        self.assertEqual(view['file_status'], 'read')
        self.assertEqual(view['source_count'], 0)
        self.assertEqual(view['material_role'], 'reference_material')
        self.assertEqual(view['declared_sources'], [])

    def test_bundle_preserves_eight_abstracts_and_two_engineering_documents(self):
        material = parse_material('pack.md', '\n'.join('Source '+str(i) for i in range(10)).encode())
        rows = [dict(title='Source '+str(i), source_id='s'+str(i),
            access_level='abstract' if i < 8 else 'full_text',
            source_type='paper' if i < 8 else 'engineering_document', start_line=i+1, end_line=i+1)
            for i in range(10)]
        mapped = configure_material(material, dict(classification='source_bundle', declared_sources=rows))
        view = material_display(mapped)
        self.assertEqual(view['paper_count'], 8)
        self.assertEqual(view['document_count'], 2)
        self.assertEqual(view['source_count'], 10)
        self.assertEqual(sum(row['access_level'] == 'abstract' for row in view['declared_sources']), 8)
        self.assertTrue(all(row['declaration_basis'] == 'user_declared' for row in view['declared_sources']))
        self.assertEqual(material['declared_sources'], [])

    def test_multiple_excerpts_of_one_source_do_not_inflate_count(self):
        material = parse_material('pack.md', b'abstract\nsecond excerpt')
        rows = [dict(title='HarnessVLN', doi='10.1234/example', access_level='abstract',
            source_type='paper', start_line=i, end_line=i) for i in (1, 2)]
        mapped = configure_material(material, dict(classification='source_bundle', declared_sources=rows))
        self.assertEqual(material_display(mapped)['source_count'], 1)
        draft = configure_material(mapped, dict(classification='draft'))
        self.assertEqual(material_display(draft)['source_count'], 0)
        self.assertEqual(material_display(draft)['file_status'], 'read')

    def test_mapping_rejects_ranges_not_read_from_file(self):
        material = parse_material('pack.md', b'abstract')
        row = dict(title='Paper', access_level='full_text', source_type='paper', start_line=1, end_line=2)
        with self.assertRaises(MaterialError):
            configure_material(material, dict(classification='source_bundle', declared_sources=[row]))
        self.assertEqual(material['declared_sources'], [])

    def test_failed_file_cannot_have_declared_content(self):
        material = parse_material('pack.txt', b'\x00')
        row = dict(title='Paper', access_level='abstract', source_type='paper', start_line=1, end_line=1)
        with self.assertRaises(MaterialError):
            configure_material(material, dict(classification='source_bundle', declared_sources=[row]))
        self.assertEqual(material_display(material)['file_status'], 'failed')

    def test_reuploading_same_evidence_and_draft_does_not_add_independent_sources(self):
        material = parse_material('pack.md', b'Paper abstract')
        mapping = dict(classification='source_bundle', declared_sources=[dict(title='Harness',
            doi='10.1234/harness', access_level='abstract', source_type='paper', start_line=1, end_line=1)])
        first = configure_material(material, mapping)
        second = configure_material(parse_material('pack-copy.md', b'Paper abstract'), mapping)
        draft = configure_material(parse_material('review.md', b'Derived review'), dict(classification='draft'))
        summary = materials_summary([first, second, draft])
        self.assertEqual(summary['file_count'], 3)
        self.assertEqual(summary['unique_file_count'], 2)
        self.assertEqual(summary['declared_source_count'], 1)
        self.assertEqual(summary['abstract_count'], 1)
        self.assertEqual(summary['draft_count'], 1)
