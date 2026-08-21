from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from literature_downloader.config import Settings
from literature_downloader.collector import _extract_pdf_urls, _semantic_pdf_url, collect_paper, download_pdf
from literature_downloader.db import Database
from literature_downloader.models import Paper
from literature_downloader.pipeline import LiteraturePipeline
from literature_downloader.verifier import verify_pdf


class CoreTests(unittest.TestCase):
  def test_extracts_standard_pdf_links_from_landing_page(self) -> None:
    html = b"""
      <html><head>
      <meta name="citation_pdf_url" content="/article/files/paper.pdf">
      <link rel="alternate" type="application/pdf" href="https://repo.example/second.pdf">
      </head></html>
    """

    urls = _extract_pdf_urls(html, "https://publisher.example/article/123")

    self.assertEqual(urls, [
        "https://publisher.example/article/files/paper.pdf",
        "https://repo.example/second.pdf",
    ])

  def test_collector_follows_pdf_discovered_on_landing_page(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "db", root / "pdfs", root / "reports", download_request_interval_ms=0)
    target = root / "source.pdf"
    calls: list[str] = []

    def fake_download(url, target_path, config, **kwargs):
      calls.append(url)
      if url.endswith("/article"):
        return {
            "ok": False,
            "path": str(target_path),
            "size": 1200,
            "error": "Not a PDF (Content-Type: text/html)",
            "status_code": 200,
            "final_url": url,
            "request_attempts": 1,
            "pdf_urls": ["https://publisher.example/article.pdf"],
        }
      return {
          "ok": True, "path": str(target), "size": 20480, "error": "",
          "status_code": 200, "final_url": url, "request_attempts": 1, "pdf_urls": [],
      }

    with patch("literature_downloader.collector.download_pdf", side_effect=fake_download):
      result = collect_paper(
          Paper(title="Landing page paper", url="https://publisher.example/article"),
          root / "pdfs",
          config,
      )

    self.assertTrue(result.ok)
    self.assertEqual(result.source, "metadata_url:html_pdf")
    self.assertEqual(calls, ["https://publisher.example/article", "https://publisher.example/article.pdf"])

  def test_collector_retries_direct_pdf_after_landing_session_is_established(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "db", root / "pdfs", root / "reports", download_request_interval_ms=0)
    calls: list[tuple[str, str]] = []

    def fake_download(url, target_path, config, **kwargs):
      referer = str(kwargs.get("referer") or "")
      calls.append((url, referer))
      if url.endswith(".pdf") and referer:
        return {"ok": True, "path": str(root / "paper.pdf"), "size": 20480, "error": ""}
      if url.endswith(".pdf"):
        return {"ok": False, "path": str(target_path), "size": 0, "error": "HTTP 403"}
      return {
          "ok": False, "path": str(target_path), "size": 1200, "error": "Not a PDF",
          "final_url": url, "pdf_urls": ["https://publisher.example/paper.pdf"],
      }

    paper = Paper(
        title="Session paper",
        pdf_url="https://publisher.example/paper.pdf",
        identifiers={"openalex_landing_urls": ["https://publisher.example/article"]},
    )
    with patch("literature_downloader.collector.download_pdf", side_effect=fake_download):
      result = collect_paper(paper, root / "pdfs", config)

    self.assertTrue(result.ok)
    self.assertEqual(calls, [
        ("https://publisher.example/paper.pdf", ""),
        ("https://publisher.example/article", ""),
        ("https://publisher.example/paper.pdf", "https://publisher.example/article"),
    ])

  def test_download_retries_transient_http_error(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(
        root, root / "db", root / "pdfs", root / "reports",
        download_retries=1, download_retry_backoff_ms=100, download_request_interval_ms=0,
    )

    class FakeResponse:
      status = 200
      headers = {"Content-Type": "application/pdf", "Content-Length": "13"}

      def __enter__(self):
        return self

      def __exit__(self, *args):
        return False

      def geturl(self):
        return "https://repo.example/paper.pdf"

      def read(self, limit):
        return b"%PDF-1.4\nabc"

    temporary_error = urllib.error.HTTPError(
        "https://repo.example/paper.pdf", 503, "Service Unavailable", {}, None
    )
    with patch("literature_downloader.collector.urllib.request.urlopen", side_effect=[temporary_error, FakeResponse()]), patch(
        "literature_downloader.collector.time.sleep"
    ):
      result = download_pdf("https://repo.example/paper.pdf", root / "paper.pdf", config)

    self.assertTrue(result["ok"])
    self.assertEqual(result["request_attempts"], 2)

  def test_download_does_not_retry_permanent_http_error(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(
        root, root / "db", root / "pdfs", root / "reports",
        download_retries=3, download_request_interval_ms=0,
    )
    forbidden = urllib.error.HTTPError(
        "https://publisher.example/paper.pdf", 403, "Forbidden", {}, None
    )
    with patch("literature_downloader.collector.urllib.request.urlopen", side_effect=forbidden) as open_url, patch(
        "literature_downloader.collector.time.sleep"
    ) as sleep:
      result = download_pdf("https://publisher.example/paper.pdf", root / "paper.pdf", config)

    self.assertFalse(result["ok"])
    self.assertEqual(result["status_code"], 403)
    self.assertEqual(result["request_attempts"], 1)
    open_url.assert_called_once()
    sleep.assert_not_called()

  def test_semantic_scholar_doi_lookup_is_skipped_without_key_or_paper_id(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "db", root / "pdfs", root / "reports")
    with patch("literature_downloader.collector._request_json") as request_json:
      url = _semantic_pdf_url(Paper(title="Paper", doi="10.1000/example"), config)

    self.assertEqual(url, "")
    request_json.assert_not_called()

  def test_database_deduplicates_by_doi(self) -> None:
    path = Path(tempfile.mkdtemp()) / "test.db"
    db = Database(path)
    task_id = db.create_task("topic", 3)
    first = db.upsert_paper(task_id, {"title": "One", "doi": "https://doi.org/10.1000/ABC", "provider": "OpenAlex"})
    second = db.upsert_paper(task_id, {"title": "One updated", "doi": "10.1000/abc", "provider": "Crossref"})
    self.assertEqual(first, second)
    self.assertEqual(len(db.list_papers(task_id)), 1)
    self.assertEqual(db.list_papers(task_id)[0]["title"], "One updated")


  def test_verifier_rejects_non_pdf(self) -> None:
    root = Path(tempfile.mkdtemp())
    path = root / "bad.pdf"
    path.write_text("<html>error</html>", encoding="utf-8")
    result = verify_pdf(Paper(title="Test", pdf_path=str(path)), Settings(root, root / "db", root / "pdfs", root / "reports"))
    self.assertEqual(result.verdict, "fail")
    self.assertTrue("signature" in result.reason or "small" in result.reason)

  def test_collector_uses_source_metadata_url_as_last_fallback(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "db", root / "pdfs", root / "reports")
    target = root / "source.pdf"
    with patch(
        "literature_downloader.collector.download_pdf",
        return_value={"ok": True, "path": str(target), "size": 123, "error": ""},
    ) as download:
      result = collect_paper(
          Paper(title="Source paper", url="https://repository.example/source.pdf"),
          root / "pdfs",
          config,
      )

    self.assertTrue(result.ok)
    self.assertEqual(result.source, "metadata_url")
    download.assert_called_once()
    self.assertEqual(download.call_args.args[0], "https://repository.example/source.pdf")

  def test_collector_tries_alternate_open_access_urls(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "db", root / "pdfs", root / "reports")
    target = root / "source.pdf"
    calls: list[str] = []

    def fake_download(url, target_path, config, **kwargs):
      calls.append(url)
      if "blocked" in url:
        return {"ok": False, "path": str(target_path), "size": 0, "error": "HTTP 403"}
      return {"ok": True, "path": str(target), "size": 123, "error": ""}

    with patch("literature_downloader.collector.download_pdf", side_effect=fake_download):
      result = collect_paper(
          Paper(
              title="Open paper",
              pdf_url="https://blocked.example/paper.pdf",
              identifiers={"openalex_pdf_urls": ["https://blocked.example/paper.pdf", "https://repository.example/paper.pdf"]},
          ),
          root / "pdfs",
          config,
      )

    self.assertTrue(result.ok)
    self.assertEqual(calls, ["https://blocked.example/paper.pdf", "https://repository.example/paper.pdf"])


  def test_pipeline_separates_search_from_download(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "literature.db", root / "pdfs", root / "reports", search_rounds=2, min_pdf_bytes=5, min_text_chars=1)
    db = Database(config.db_path)
    pipeline = LiteraturePipeline(config, db)
    task_id = pipeline.create_task("topic", 2)
    local_hit = {
        "title": "A verified local paper",
        "provider": "LocalLibrary",
        "providers": ["LocalLibrary"],
        "pdf_status": "verified",
        "pdf_path": str(root / "local.pdf"),
        "doi": "10.1000/local",
    }
    search_result = {
        "topic": "topic", "query_variants": ["topic"], "local_results": [local_hit],
        "api_results": {"OpenAlex": [{"title": "An API paper", "provider": "OpenAlex"}]},
        "papers": [{"title": "A paper", "provider": "arXiv", "arxiv_id": "1234.5678", "abstract": "topic"}],
        "need_download": [], "provider_counts": {}, "local_hits": 0, "errors": {},
    }
    with patch("literature_downloader.pipeline.search_literature", return_value=search_result):
        pipeline.search(task_id)
    search_state = pipeline.status(task_id)["search"]
    self.assertEqual(pipeline.status(task_id)["status"], "ready:download")
    self.assertEqual(search_state["local_results"][0]["title"], "A verified local paper")
    self.assertEqual(search_state["api_results"]["OpenAlex"][0]["title"], "An API paper")

    good_pdf = root / "good.pdf"
    good_pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    calls = {"count": 0}

    def fake_collect(paper, target, config):
        calls["count"] += 1
        from literature_downloader.models import DownloadResult
        return DownloadResult(True, path=str(good_pdf), size=good_pdf.stat().st_size, source="arXiv")

    from literature_downloader.models import VerificationResult
    with patch("literature_downloader.pipeline.collect_paper", side_effect=fake_collect), patch(
        "literature_downloader.pipeline.verify_pdf",
        return_value=VerificationResult("A paper", "pass", str(good_pdf), good_pdf.stat().st_size, 10),
    ):
        final = pipeline.download(task_id)
    self.assertEqual(final["status"], "completed")
    self.assertEqual(final["collection"]["cumulative_verified"], 1)
    self.assertTrue(Path(final["reports"]["pdf_zip"]).exists())

  def test_stage_can_only_be_claimed_once(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
    pipeline = LiteraturePipeline(config, Database(config.db_path))
    task_id = pipeline.create_task("topic", 2)
    self.assertTrue(pipeline.claim_stage(task_id, "search"))
    self.assertFalse(pipeline.claim_stage(task_id, "search"))

  def test_restart_clears_running_tasks(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
    db = Database(config.db_path)
    waiting_search = db.create_task("running search", 2, email="user@example.com")
    waiting_collect = db.create_task("running collect", 2, email="user@example.com")
    db.update_task(waiting_search, status="running")
    db.update_task(waiting_collect, status="running")

    self.assertEqual(db.get_active_task("user@example.com")["id"], waiting_search)
    self.assertEqual(db.interrupt_running_tasks(), 2)
    self.assertEqual(db.get_task(waiting_search)["status"], "failed")
    self.assertEqual(db.get_task(waiting_collect)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
