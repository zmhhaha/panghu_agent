from __future__ import annotations

import unittest

from literature_downloader.reports import (
    format_collection_report,
    format_final_report,
    format_search_report,
    format_verification_report,
)


class ReportFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paper = {
            "title": "A paper about hydrometallurgy",
            "authors": "A. Author",
            "date": "2026-01-01",
            "provider": "OpenAlex",
            "providers": ["OpenAlex", "Crossref"],
            "venue": "Journal of Metals",
            "doi": "10.1000/example",
            "arxiv_id": "2601.12345",
            "url": "https://openalex.org/W123",
            "pdf_url": "https://publisher.example/paper.pdf",
            "identifiers": {"openalex": "https://openalex.org/W123"},
            "abstract": "A useful abstract.",
            "pdf_status": "pending_download",
        }

    def test_search_report_keeps_source_metadata(self) -> None:
        result = {
            "topic": "hydrometallurgy",
            "query_variants": ["hydrometallurgy"],
            "local_hits": 0,
            "papers": [self.paper],
            "need_download": [self.paper],
            "api_results": {"OpenAlex": [self.paper]},
            "provider_counts": {"OpenAlex": 1},
            "errors": {},
        }

        report = format_search_report(result)

        self.assertIn("来源数据库: OpenAlex", report)
        self.assertIn("元数据 URL: <https://openalex.org/W123>", report)
        self.assertIn("公开 PDF URL: <https://publisher.example/paper.pdf>", report)
        self.assertIn("DOI: 10.1000/example", report)

    def test_collection_report_keeps_attempt_urls_and_local_artifact(self) -> None:
        row = {
            **self.paper,
            "ok": True,
            "source": "Unpaywall",
            "path": "/data/literature/pdfs/task/paper.pdf",
            "size": 20480,
            "attempts": [
                {
                    "source": "DOI",
                    "url": "https://doi.org/10.1000/example",
                    "ok": False,
                    "size": 0,
                    "elapsed_ms": 100,
                    "error": "HTTP 403",
                },
                {
                    "source": "Unpaywall",
                    "url": "https://repository.example/paper.pdf",
                    "ok": True,
                    "size": 20480,
                    "elapsed_ms": 200,
                    "error": "",
                },
            ],
        }

        report = format_collection_report([row], 1)

        self.assertIn("实际下载来源: Unpaywall", report)
        self.assertIn("实际下载 URL: <https://repository.example/paper.pdf>", report)
        self.assertIn("https://doi.org/10.1000/example", report)
        self.assertIn("HTTP 403", report)
        self.assertIn("本地存储路径: /data/literature/pdfs/task/paper.pdf", report)

    def test_verification_and_final_reports_keep_source_chain(self) -> None:
        verification = {
            **self.paper,
            "title": self.paper["title"],
            "download_source": "Unpaywall",
            "download_url": "https://repository.example/paper.pdf",
            "verdict": "pass",
            "path": "/data/literature/pdfs/task/paper.pdf",
            "size": 20480,
            "text_chars": 4000,
            "notes": "readable text extracted",
        }
        collection = {
            "round": 1,
            "attempted": 1,
            "downloaded": 1,
            "download_failed": 0,
            "verified": 1,
            "papers": [
                {
                    **self.paper,
                    "ok": True,
                    "source": "Unpaywall",
                    "path": "/data/literature/pdfs/task/paper.pdf",
                    "size": 20480,
                    "attempts": [
                        {
                            "source": "Unpaywall",
                            "url": "https://repository.example/paper.pdf",
                            "ok": True,
                            "size": 20480,
                        }
                    ],
                }
            ],
            "verification": [verification],
        }
        search = {
            "total": 1,
            "local_hits": 0,
            "need_download": 1,
            "by_provider": {"OpenAlex": 1},
            "papers": [self.paper],
        }
        verified = [{**self.paper, "verification_status": "pass", "pdf_path": verification["path"]}]

        verification_report = format_verification_report([verification], 1)
        final_report = format_final_report("hydrometallurgy", [collection], verified, [], search=search)

        for report in (verification_report, final_report):
            self.assertIn("来源数据库: OpenAlex", report)
            self.assertIn("https://repository.example/paper.pdf", report)
            self.assertIn("/data/literature/pdfs/task/paper.pdf", report)

    def test_final_report_lists_local_library_hits_with_full_metadata(self) -> None:
        local_paper = {
            **self.paper,
            "provider": "LocalLibrary",
            "providers": ["LocalLibrary"],
            "pdf_status": "verified",
            "verification_status": "pass",
            "pdf_path": "/data/literature/pdfs/shared/local-1.pdf",
            "source_record": {"source": "shared-library", "record_id": "local-1"},
        }
        search = {
            "total": 1,
            "local_hits": 1,
            "need_download": 0,
            "by_provider": {},
            "local_results": [local_paper],
            "papers": [local_paper],
        }

        report = format_final_report("hydrometallurgy", [], [local_paper], [], search=search)

        self.assertIn("本地库命中文献", report)
        self.assertIn("A paper about hydrometallurgy", report)
        self.assertIn("10.1000/example", report)
        self.assertIn("shared-library", report)
        self.assertIn("当前 PDF 状态: verified", report)
        self.assertIn("/data/literature/pdfs/shared/local-1.pdf", report)
        self.assertIn("已有已校验 PDF，本轮未重新下载", report)


if __name__ == "__main__":
    unittest.main()
