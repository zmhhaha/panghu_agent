from __future__ import annotations

import urllib.parse
import unittest
from unittest.mock import MagicMock, patch

from tools.academic import build_query_variants, deduplicate_papers, make_paper, rank_papers
from tools.academic.providers import search_openalex, search_pubmed
from tools.academic.search import search_academic
from tools.academic_tools import (
    AcademicMultiFetchTool,
    AcademicSearchTool,
    ArxivSearchTool,
    PubMedSearchTool,
    SemanticScholarSearchTool,
)


class AcademicCoreTests(unittest.TestCase):
    def test_query_builder_does_not_add_unrelated_etching_terms(self):
        variants = build_query_variants("量子计算研究进展")

        self.assertTrue(variants)
        self.assertEqual(variants[0], "量子计算")
        self.assertNotIn("etch", " ".join(variants).lower())

    def test_deduplicate_merges_doi_metadata_and_providers(self):
        records = [
            make_paper(
                "OpenAlex",
                title="Useful Paper",
                doi="https://doi.org/10.1000/ABC",
                abstract="short",
                cited_by_count=3,
            ),
            make_paper(
                "Semantic Scholar",
                title="Useful Paper",
                doi="DOI:10.1000/abc",
                abstract="a much longer abstract",
                cited_by_count=8,
                open_access=True,
            ),
        ]

        merged = deduplicate_papers(records)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["providers"], ["OpenAlex", "Semantic Scholar"])
        self.assertEqual(merged[0]["cited_by_count"], 8)
        self.assertTrue(merged[0]["open_access"])
        self.assertEqual(merged[0]["abstract"], "a much longer abstract")

    def test_deduplicate_matches_title_when_only_one_source_has_doi(self):
        records = [
            make_paper("Crossref", title="The Same Paper", doi="10.1000/same"),
            make_paper("PubMed", title="The same paper", abstract="full abstract"),
        ]

        merged = deduplicate_papers(records)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["providers"], ["Crossref", "PubMed"])
        self.assertEqual(merged[0]["abstract"], "full abstract")

    def test_ranking_keeps_relevance_ahead_of_citations(self):
        records = [
            make_paper("A", title="Quantum error correction", cited_by_count=1),
            make_paper("B", title="Unrelated highly cited biology paper", cited_by_count=100000),
        ]

        ranked = rank_papers(records, ["quantum error correction"])

        self.assertEqual(ranked[0]["provider"], "A")

    def test_search_academic_merges_providers(self):
        def openalex(query, limit, **kwargs):
            return [make_paper("OpenAlex", title="Shared Result", doi="10.1/shared")]

        def semantic(query, limit, **kwargs):
            return [make_paper("Semantic Scholar", title="Shared Result", doi="10.1/shared")]

        with patch.dict(
            "tools.academic.search.PROVIDER_SEARCHERS",
            {"openalex": openalex, "semantic_scholar": semantic},
            clear=True,
        ):
            result = search_academic(
                "quantum computing",
                providers=["openalex", "semantic_scholar"],
            )

        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(
            result["papers"][0]["providers"],
            ["OpenAlex", "Semantic Scholar"],
        )

    def test_search_academic_keeps_partial_results_and_error(self):
        def working(query, limit, **kwargs):
            return [make_paper("OpenAlex", title="Quantum result")]

        def failing(query, limit, **kwargs):
            raise TimeoutError("provider unavailable")

        with patch.dict(
            "tools.academic.search.PROVIDER_SEARCHERS",
            {"openalex": working, "crossref": failing},
            clear=True,
        ):
            result = search_academic(
                "quantum computing",
                providers=["openalex", "crossref"],
            )

        self.assertEqual(len(result["papers"]), 1)
        self.assertIn("crossref", result["errors"])


class AcademicProviderAdapterTests(unittest.TestCase):
    def test_openalex_adapter_normalizes_schema(self):
        payload = {
            "results": [{
                "id": "https://openalex.org/W1",
                "display_name": "A <b>Useful</b> Paper",
                "publication_date": "2025-01-02",
                "doi": "https://doi.org/10.1000/TEST",
                "cited_by_count": 12,
                "authorships": [{"author": {"display_name": "Ada Author"}}],
                "primary_location": {
                    "landing_page_url": "https://example.org/paper",
                    "pdf_url": "https://example.org/paper.pdf",
                    "source": {"display_name": "Journal"},
                },
                "open_access": {"is_oa": True},
                "abstract_inverted_index": {"Useful": [1], "Evidence": [0]},
            }]
        }
        with patch("tools.academic.providers.request_json", return_value=payload):
            records = search_openalex("useful", 5)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "A Useful Paper")
        self.assertEqual(records[0]["doi"], "10.1000/test")
        self.assertEqual(records[0]["abstract"], "Evidence Useful")
        self.assertTrue(records[0]["open_access"])

    def test_pubmed_adapter_keeps_all_structured_abstract_sections(self):
        search_payload = {"esearchresult": {"idlist": ["12345678"]}}
        article_xml = """
        <PubmedArticleSet><PubmedArticle>
          <MedlineCitation><PMID>12345678</PMID><Article>
            <ArticleTitle>Structured study</ArticleTitle>
            <Abstract>
              <AbstractText Label="BACKGROUND">First section.</AbstractText>
              <AbstractText Label="RESULTS">Second <i>section</i>.</AbstractText>
            </Abstract>
            <AuthorList><Author><ForeName>Ada</ForeName><LastName>Author</LastName></Author></AuthorList>
            <Journal><Title>Test Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
          </Article></MedlineCitation>
          <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/pubmed</ArticleId></ArticleIdList></PubmedData>
        </PubmedArticle></PubmedArticleSet>
        """
        with patch("tools.academic.providers.request_json", return_value=search_payload), patch(
            "tools.academic.providers.request_text", return_value=article_xml
        ), patch("tools.academic.providers.time.sleep", return_value=None):
            records = search_pubmed("structured", 5)

        self.assertEqual(len(records), 1)
        self.assertIn("BACKGROUND: First section.", records[0]["abstract"])
        self.assertIn("RESULTS: Second section.", records[0]["abstract"])
        self.assertEqual(records[0]["doi"], "10.1000/pubmed")


class ExistingAcademicToolRegressionTests(unittest.TestCase):
    def test_arxiv_advanced_query_is_not_prefixed_with_all(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        )
        response.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            ArxivSearchTool()._run("ti:transformer AND cat:cs.CL", max_results=3)

        request = urlopen.call_args.args[0]
        params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        self.assertEqual(params["search_query"], ["ti:transformer AND cat:cs.CL"])
        self.assertTrue(request.full_url.startswith("https://"))

    def test_pubmed_has_no_implicit_stale_date_filter(self):
        response = MagicMock()
        response.json.return_value = {"esearchresult": {"idlist": [], "count": "0"}}
        response.raise_for_status.return_value = None
        requests_module = MagicMock()
        requests_module.get.return_value = response

        with patch("tools.academic_tools._safe_import_requests", return_value=requests_module), patch(
            "tools.academic_tools.time.sleep", return_value=None
        ):
            PubMedSearchTool()._run("cancer immunotherapy", max_results=3)

        self.assertEqual(
            requests_module.get.call_args.kwargs["params"]["term"],
            "cancer immunotherapy",
        )

    def test_semantic_search_requests_open_access_fields(self):
        response = MagicMock()
        response.json.return_value = {"data": [], "total": 0}
        response.raise_for_status.return_value = None
        requests_module = MagicMock()
        requests_module.get.return_value = response

        with patch("tools.academic_tools._safe_import_requests", return_value=requests_module):
            SemanticScholarSearchTool()._run("test query", limit=3)

        fields = requests_module.get.call_args.kwargs["params"]["fields"]
        self.assertIn("isOpenAccess", fields)
        self.assertIn("openAccessPdf", fields)

    def test_multi_fetch_accepts_prefixed_doi(self):
        with patch(
            "tools.academic_tools.CrossrefLookupTool._run",
            return_value="crossref result",
        ) as lookup:
            output = AcademicMultiFetchTool()._run("DOI:10.1000/example")

        lookup.assert_called_once_with("10.1000/example")
        self.assertIn("crossref result", output)

    def test_aggregate_tool_formats_structured_results(self):
        fake_result = {
            "topic": "topic",
            "query_variants": ["topic"],
            "papers": [
                make_paper(
                    "OpenAlex",
                    title="Paper title",
                    doi="10.1000/test",
                    abstract="Evidence",
                )
                | {"relevance_score": 3.0}
            ],
            "provider_counts": {"openalex": 1},
            "errors": {},
        }
        with patch("tools.academic_tools.search_academic", return_value=fake_result):
            output = AcademicSearchTool()._run("topic", max_results=5)

        self.assertIn("[P1] Paper title", output)
        self.assertIn("10.1000/test", output)


if __name__ == "__main__":
    unittest.main()
