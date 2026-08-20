from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_downloader.config import Settings
from literature_downloader.db import Database
from literature_downloader.searcher import search_literature
from tools.academic.query import build_query_variants


class SearchExpansionTests(unittest.TestCase):
    def test_inp_topic_uses_clean_domain_variants(self) -> None:
        variants = build_query_variants("InP的干法刻蚀研究进展_目前难点_及发展方向", 6)

        self.assertEqual(variants[0], "InP plasma etching")
        self.assertIn("InP ICP etching", variants)
        self.assertNotIn("_", " ".join(variants))
        self.assertNotIn("目前", " ".join(variants))

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

        self.assertEqual(oa.call_count, 6)
        self.assertEqual(cr.call_count, 6)
        self.assertEqual(ax.call_count, 6)
        self.assertEqual(ss.call_count, 6)
        self.assertGreaterEqual(len(result["papers"]), 20)
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
        with patch("literature_downloader.searcher.search_openalex", return_value=[unrelated]), patch(
            "literature_downloader.searcher.search_crossref", return_value=[]
        ), patch("literature_downloader.searcher.search_arxiv", return_value=[]), patch(
            "literature_downloader.searcher.search_semantic_scholar", return_value=[]
        ):
            result = search_literature("InP 的干法刻蚀研究进展", db, config=config)
        self.assertEqual(result["papers"], [])


if __name__ == "__main__":
    unittest.main()
