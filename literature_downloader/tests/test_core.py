from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_downloader.config import Settings
from literature_downloader.collector import collect_paper
from literature_downloader.db import Database
from literature_downloader.models import Paper
from literature_downloader.pipeline import LiteraturePipeline
from literature_downloader.verifier import verify_pdf


class CoreTests(unittest.TestCase):
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

    def fake_download(url, target_path, config):
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


  def test_pipeline_retries_failed_download(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "literature.db", root / "pdfs", root / "reports", max_rounds=2, min_pdf_bytes=5, min_text_chars=1)
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
        "papers": [{"title": "A paper", "provider": "arXiv", "arxiv_id": "1234.5678", "abstract": ""}],
        "need_download": [], "provider_counts": {}, "local_hits": 0, "errors": {},
    }
    with patch("literature_downloader.pipeline.search_literature", return_value=search_result):
        pipeline.search(task_id)
    search_state = pipeline.status(task_id)["search"]
    self.assertEqual(pipeline.status(task_id)["status"], "waiting:search_approval")
    self.assertEqual(search_state["local_results"][0]["title"], "A verified local paper")
    self.assertEqual(search_state["api_results"]["OpenAlex"][0]["title"], "An API paper")

    good_pdf = root / "good.pdf"
    good_pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    calls = {"count": 0}

    def fake_collect(paper, target, config):
        calls["count"] += 1
        if calls["count"] == 1:
            from literature_downloader.models import DownloadResult
            return DownloadResult(False, error="network")
        from literature_downloader.models import DownloadResult
        return DownloadResult(True, path=str(good_pdf), size=good_pdf.stat().st_size, source="arXiv")

    from literature_downloader.models import VerificationResult
    with patch("literature_downloader.pipeline.collect_paper", side_effect=fake_collect), patch(
        "literature_downloader.pipeline.verify_pdf",
        return_value=VerificationResult("A paper", "pass", str(good_pdf), good_pdf.stat().st_size, 10),
    ):
        first = pipeline.collect_round(task_id)
        self.assertEqual(first["status"], "waiting:collect_approval")
        final = pipeline.collect_round(task_id)
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

  def test_restart_clears_legacy_waiting_tasks(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
    db = Database(config.db_path)
    waiting_search = db.create_task("waiting search", 2, email="user@example.com")
    waiting_collect = db.create_task("waiting collect", 2, email="user@example.com")
    db.update_task(waiting_search, status="waiting:search_approval")
    db.update_task(waiting_collect, status="waiting:collect_approval")

    self.assertIsNone(db.get_active_task("user@example.com"))
    self.assertEqual(db.interrupt_running_tasks(), 2)
    self.assertEqual(db.get_task(waiting_search)["status"], "failed")
    self.assertEqual(db.get_task(waiting_collect)["status"], "failed")

  def test_run_all_advances_without_user_approval(self) -> None:
    root = Path(tempfile.mkdtemp())
    config = Settings(root, root / "literature.db", root / "pdfs", root / "reports")
    pipeline = LiteraturePipeline(config, Database(config.db_path))
    task_id = pipeline.create_task("topic", 2)
    with patch.object(
        pipeline,
        "search",
        return_value={"status": "waiting:search_approval"},
    ) as search, patch.object(
        pipeline,
        "collect_round",
        return_value={"status": "completed"},
    ) as collect:
      result = pipeline.run_all(task_id)
    self.assertEqual(result["status"], "completed")
    search.assert_called_once_with(task_id, None)
    collect.assert_called_once_with(task_id, None)


if __name__ == "__main__":
    unittest.main()
