from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_downloader.config import Settings
from literature_downloader.db import Database
from literature_downloader.searcher import search_literature
from literature_downloader.search_planner import create_search_plan
from tools.academic.providers import search_openalex


class SearchExpansionTests(unittest.TestCase):
    def test_openalex_preserves_all_pdf_and_landing_locations(self) -> None:
        payload = {
            "results": [{
                "id": "https://openalex.org/W1",
                "display_name": "Open paper",
                "doi": "https://doi.org/10.1000/open",
                "primary_location": {
                    "landing_page_url": "https://publisher.example/article",
                    "pdf_url": "https://publisher.example/article.pdf",
                    "source": {"display_name": "Journal"},
                },
                "best_oa_location": {
                    "landing_page_url": "https://repository.example/record",
                    "pdf_url": "https://repository.example/paper.pdf",
                },
                "locations": [],
            }]
        }
        with patch("tools.academic.providers.request_json", return_value=payload):
            paper = search_openalex("topic", 10)[0]

        self.assertEqual(paper["identifiers"]["openalex_pdf_urls"], [
            "https://repository.example/paper.pdf",
            "https://publisher.example/article.pdf",
        ])
        self.assertEqual(paper["identifiers"]["openalex_landing_urls"], [
            "https://repository.example/record",
            "https://publisher.example/article",
        ])

    def test_inp_topic_uses_clean_domain_variants(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = Settings(root, root / "literature.db", root / "pdfs", root / "reports", max_search_variants=6)
        variants = create_search_plan(
            "InP的干法刻蚀研究进展_目前难点_及发展方向", config=config, max_variants=6
        )["query_variants"]

        self.assertTrue(variants)
        self.assertIn("InP", variants[0])
        self.assertNotIn("plasma etching", " ".join(variants).lower())
        self.assertNotIn("_", " ".join(variants))

    def test_search_uses_configured_variant_count_and_expanded_limit(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = Settings(
            root,
            root / "literature.db",
            root / "pdfs",
            root / "reports",
            search_limit=100,
            per_provider=20,
            max_search_variants=6,
        )
        db = Database(config.db_path)

        def record(provider: str, query: str, index: int) -> dict[str, object]:
            return {
                "title": f"{provider} {query} {index}",
                "provider": provider,
                "doi": f"10.1000/{provider.lower().replace(' ', '-')}-{index}-{abs(hash(query))}",
                "url": f"https://example.org/{provider}/{index}/{abs(hash(query))}",
                "pdf_url": f"https://example.org/{provider}/{index}/{abs(hash(query))}.pdf",
                "open_access": True,
            }

        def openalex(query: str, limit: int, *, email: str = "") -> list[dict[str, object]]:
            self.assertEqual(limit, 20)
            return [record("OpenAlex", query, 1)]

        def crossref(query: str, limit: int, *, email: str = "") -> list[dict[str, object]]:
            self.assertEqual(limit, 20)
            return [record("Crossref", query, 1)]

        def arxiv(query: str, limit: int) -> list[dict[str, object]]:
            self.assertEqual(limit, 20)
            return [record("arXiv", query, 1)]

        def semantic(query: str, limit: int, *, api_key: str = "") -> list[dict[str, object]]:
            self.assertEqual(limit, 20)
            return [record("Semantic Scholar", query, 1)]

        with patch("literature_downloader.searcher.search_openalex", side_effect=openalex) as oa, patch(
            "literature_downloader.searcher.search_crossref", side_effect=crossref
        ) as cr, patch("literature_downloader.searcher.search_arxiv", side_effect=arxiv) as ax, patch(
            "literature_downloader.searcher.search_semantic_scholar", side_effect=semantic
        ) as ss:
            result = search_literature(
                "InP的干法刻蚀研究进展_目前难点_及发展方向",
                db,
                config=config,
            )

        self.assertEqual(oa.call_count, len(result["query_variants"]))
        self.assertEqual(cr.call_count, len(result["query_variants"]))
        self.assertEqual(ax.call_count, len(result["query_variants"]))
        self.assertEqual(ss.call_count, len(result["query_variants"]))
        self.assertGreaterEqual(len(result["papers"]), 1)
        self.assertLessEqual(len(result["papers"]), 100)

    def test_zero_relevance_provider_matches_are_not_downloaded(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
        db = Database(config.db_path)
        local_task = db.create_task("previous topic", 1)
        unrelated = {
            "title": "曲尼司特的药理作用研究进展",
            "provider": "OpenAlex",
            "doi": "10.1000/unrelated",
            "url": "https://example.org/unrelated",
        }
        db.upsert_paper(
            local_task,
            {**unrelated, "pdf_status": "verified", "pdf_path": str(root / "unrelated.pdf")},
        )
        with patch("literature_downloader.searcher.search_openalex", return_value=[unrelated]), patch(
            "literature_downloader.searcher.search_crossref", return_value=[]
        ), patch("literature_downloader.searcher.search_arxiv", return_value=[]), patch(
            "literature_downloader.searcher.search_semantic_scholar", return_value=[]
        ):
            result = search_literature("InP的干法刻蚀研究进展", db, config=config)

        self.assertEqual(result["papers"], [])
        self.assertEqual(result["need_download"], [])

    def test_local_library_copies_are_deduplicated(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
        db = Database(config.db_path)
        first_task = db.create_task("previous topic 1", 1)
        second_task = db.create_task("previous topic 2", 1)
        paper = {
            "title": "InP dry etching review",
            "authors": "A. Author",
            "provider": "OpenAlex",
            "doi": "10.1000/inp-review",
            "abstract": "InP plasma etching and dry etching.",
            "pdf_status": "verified",
            "pdf_path": str(root / "review.pdf"),
        }
        db.upsert_paper(first_task, paper)
        db.upsert_paper(second_task, paper)
        with patch("literature_downloader.searcher.search_openalex", return_value=[]), patch(
            "literature_downloader.searcher.search_crossref", return_value=[]
        ), patch("literature_downloader.searcher.search_arxiv", return_value=[]), patch(
            "literature_downloader.searcher.search_semantic_scholar", return_value=[]
        ):
            result = search_literature("InP dry etching", db, config=config)

        self.assertEqual(result["local_hits"], 1)
        self.assertEqual(len(result["local_results"]), 1)

    def test_global_library_paper_is_reused_by_search(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
        db = Database(config.db_path)
        pdf_path = root / "review.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\ncontent")
        db.upsert_library_paper({
            "title": "InP dry etching review",
            "authors": "A. Author",
            "doi": "10.1000/inp-review",
            "abstract": "InP plasma etching and dry etching.",
            "pdf_status": "verified",
            "pdf_path": str(pdf_path),
            "verification_status": "pass",
        })
        with patch("literature_downloader.searcher.search_openalex", return_value=[]), patch(
            "literature_downloader.searcher.search_crossref", return_value=[]
        ), patch("literature_downloader.searcher.search_arxiv", return_value=[]), patch(
            "literature_downloader.searcher.search_semantic_scholar", return_value=[]
        ):
            result = search_literature("InP dry etching", db, config=config)

        self.assertEqual(result["local_hits"], 1)
        self.assertEqual(len(result["need_download"]), 0)
        self.assertEqual(result["papers"][0]["pdf_path"], str(pdf_path))

    def test_inp_scope_rejects_other_materials_with_same_etch_terms(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
        db = Database(config.db_path)
        unrelated = {
            "title": "ICP etching of lithium niobate",
            "abstract": "Reactive ion etching and plasma process optimization.",
            "provider": "OpenAlex",
            "doi": "10.1000/lithium-niobate",
            "url": "https://example.org/unrelated",
        }
        plan = {
            "query_variants": ["InP plasma etching"],
            "llm": {"used": True, "status": "used"},
            "scope_requirements": [
                {"name": "material", "terms": ["InP", "indium phosphide"], "required": True},
                {"name": "process", "terms": ["plasma etching"], "required": True},
            ],
        }
        with patch("literature_downloader.searcher.search_openalex", return_value=[unrelated]), patch(
            "literature_downloader.searcher.search_crossref", return_value=[]
        ), patch("literature_downloader.searcher.search_arxiv", return_value=[]), patch(
            "literature_downloader.searcher.search_semantic_scholar", return_value=[]
        ), patch("literature_downloader.searcher.create_search_plan", return_value=plan):
            result = search_literature("InP 的干法刻蚀研究进展", db, config=config)
        self.assertEqual(result["papers"], [])


if __name__ == "__main__":
    unittest.main()
