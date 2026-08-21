from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from literature_downloader.config import Settings
from literature_downloader.db import Database
from literature_downloader.models import VerificationResult
from literature_downloader.pipeline import LiteraturePipeline
from literature_downloader.scihub_job import build_input_config_map, build_job


class SciHubJobManifestTests(unittest.TestCase):
    def test_input_config_map_has_one_identifier_per_paper(self) -> None:
        config_map = build_input_config_map(
            "literature-downloader",
            "scihub-input",
            {12: "10.1000/example", 13: "https://arxiv.org/abs/1234.5678"},
        )
        self.assertEqual(config_map["metadata"]["namespace"], "literature-downloader")
        self.assertEqual(config_map["data"], {
            "paper-12.txt": "10.1000/example\n",
            "paper-13.txt": "https://arxiv.org/abs/1234.5678\n",
        })

    def test_job_isolated_output_and_shared_pvc(self) -> None:
        job = build_job(
            namespace="literature-downloader",
            name="scihub-download-task-r1",
            input_config_map="scihub-input",
            image="registry/scihub-cli:latest",
            pvc_name="scihub-papers-pvc",
            task_id="task-123",
            round_num=1,
            request_timeout=30,
            job_timeout_seconds=600,
            retries=2,
            email="panghuer001@163.com",
        )
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        script = container["args"][0]
        self.assertEqual(job["metadata"]["namespace"], "literature-downloader")
        self.assertEqual(pod["volumes"][1]["persistentVolumeClaim"]["claimName"], "scihub-papers-pvc")
        self.assertIn("/app/papers/jobs/task-123/round-1/$paper_id", script)
        self.assertIn("scihub-cli", script)
        self.assertIn("--parallel 1", script)
        self.assertIn("--timeout 30", script)
        self.assertIn("--enable-core", script)
        self.assertIn("--trace-html", script)
        self.assertIn('--email \"$SCIHUB_EMAIL\"', script)
        self.assertIn('exit "$infra_failed"', script)
        self.assertEqual(container["env"], [{"name": "SCIHUB_EMAIL", "value": "panghuer001@163.com"}])

    def test_pipeline_reads_successful_job_output_from_shared_pvc(self) -> None:
        root = Path(tempfile.mkdtemp())
        shared = root / "shared"
        config = Settings(
            root / "data",
            root / "literature.db",
            root / "pdfs",
            root / "reports",
            download_backend="scihub-job",
            scihub_papers_dir=shared,
            min_pdf_bytes=5,
            min_text_chars=1,
        )
        db = Database(config.db_path)
        pipeline = LiteraturePipeline(config, db)
        task_id = pipeline.create_task("topic", 1)
        db.upsert_paper(task_id, {"title": "Paper", "doi": "10.1000/example", "pdf_status": "pending_download"})
        db.update_task(task_id, status="ready:download")

        class FakeClient:
            def create_config_map(self, namespace, manifest):
                return manifest

            def create_job(self, namespace, manifest):
                output = shared / "jobs" / task_id / "round-1" / "paper-1"
                output.mkdir(parents=True, exist_ok=True)
                (output / "paper.pdf").write_bytes(b"%PDF-1.4\ncontent")
                return manifest

            def wait_for_job(self, namespace, name, timeout_seconds, poll_seconds):
                return {"status": {"succeeded": 1}}

            def delete_config_map(self, namespace, name):
                return None

        with patch("literature_downloader.pipeline.KubernetesJobClient", FakeClient), patch(
            "literature_downloader.pipeline.verify_pdf",
            return_value=VerificationResult("Paper", "pass", str(shared / "paper.pdf"), 16, 10),
        ):
            result = pipeline.collect_round(task_id)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["collection"]["cumulative_verified"], 1)
        paper = db.list_papers(task_id)[0]
        self.assertEqual(paper["pdf_status"], "verified")
        self.assertTrue(paper["pdf_path"].endswith("paper.pdf"))

    def test_hybrid_backend_prefers_direct_download_and_falls_back_per_paper(self) -> None:
        root = Path(tempfile.mkdtemp())
        shared = root / "shared"
        direct_pdf = root / "direct.pdf"
        direct_pdf.write_bytes(b"%PDF-1.4\ndirect")
        config = Settings(
            root / "data",
            root / "literature.db",
            root / "pdfs",
            root / "reports",
            download_backend="hybrid",
            scihub_papers_dir=shared,
            min_pdf_bytes=5,
            min_text_chars=1,
        )
        db = Database(config.db_path)
        pipeline = LiteraturePipeline(config, db)
        task_id = pipeline.create_task("topic", 1)
        direct_id = db.upsert_paper(task_id, {"title": "Direct", "doi": "10.1000/direct", "pdf_status": "pending_download"})
        fallback_id = db.upsert_paper(task_id, {"title": "Fallback", "doi": "10.1000/fallback", "pdf_status": "pending_download"})
        db.update_task(task_id, status="ready:download")
        input_data: dict[str, str] = {}

        class FakeClient:
            def create_config_map(self, namespace, manifest):
                input_data.update(manifest["data"])
                return manifest

            def create_job(self, namespace, manifest):
                output = shared / "jobs" / task_id / "round-1" / f"paper-{fallback_id}"
                output.mkdir(parents=True, exist_ok=True)
                (output / "fallback.pdf").write_bytes(b"%PDF-1.4\nfallback")
                return manifest

            def wait_for_job(self, namespace, name, timeout_seconds, poll_seconds):
                return {"status": {"succeeded": 1}}

            def delete_config_map(self, namespace, name):
                return None

        def collect(paper, target, config):
            from literature_downloader.models import DownloadResult
            if paper.doi == "10.1000/direct":
                return DownloadResult(
                    True,
                    path=str(direct_pdf),
                    size=direct_pdf.stat().st_size,
                    source="openalex",
                    attempts=[{"source": "openalex", "ok": True, "size": direct_pdf.stat().st_size}],
                )
            return DownloadResult(
                False,
                error="no direct PDF",
                source="openalex",
                attempts=[{"source": "openalex", "ok": False, "error": "no direct PDF"}],
            )

        def verify(paper, config):
            path = Path(paper.pdf_path)
            return VerificationResult(paper.title, "pass", str(path), path.stat().st_size, 10)

        with patch("literature_downloader.pipeline.collect_paper", side_effect=collect), patch(
            "literature_downloader.pipeline.KubernetesJobClient", FakeClient
        ), patch("literature_downloader.pipeline.verify_pdf", side_effect=verify):
            result = pipeline.collect_round(task_id)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["collection"]["rounds"][0]["attempted"], 2)
        self.assertEqual(result["collection"]["rounds"][0]["downloaded"], 2)
        self.assertEqual(len(result["collection"]["rounds"][0]["verification"]), 2)
        self.assertEqual(set(input_data), {f"paper-{fallback_id}.txt"})
        self.assertEqual(db.get_paper(direct_id)["pdf_status"], "verified")
        self.assertEqual(db.get_paper(fallback_id)["pdf_status"], "verified")
        with db.connect() as conn:
            attempts = conn.execute(
                "SELECT source FROM download_attempts WHERE task_id = ? AND paper_id = ? ORDER BY id",
                (task_id, fallback_id),
            ).fetchall()
        self.assertEqual([attempt["source"] for attempt in attempts], ["openalex", "scihub-cli"])

    def test_successful_job_is_cached_and_reused_by_another_task(self) -> None:
        root = Path(tempfile.mkdtemp())
        shared = root / "shared"
        config = Settings(
            root / "data",
            root / "literature.db",
            root / "pdfs",
            root / "reports",
            download_backend="scihub-job",
            scihub_papers_dir=shared,
            min_pdf_bytes=5,
            min_text_chars=1,
        )
        db = Database(config.db_path)
        pipeline = LiteraturePipeline(config, db)
        first_task = pipeline.create_task("topic one", 1)
        first_id = db.upsert_paper(
            first_task,
            {"title": "Paper", "doi": "10.1000/example", "pdf_status": "pending_download"},
        )
        db.update_task(first_task, status="ready:download")
        job_creations: list[str] = []

        class FakeClient:
            def create_config_map(self, namespace, manifest):
                return manifest

            def create_job(self, namespace, manifest):
                job_creations.append(manifest["metadata"]["name"])
                output = shared / "jobs" / first_task / "round-1" / f"paper-{first_id}"
                output.mkdir(parents=True, exist_ok=True)
                (output / "paper.pdf").write_bytes(b"%PDF-1.4\ncontent")
                return manifest

            def wait_for_job(self, namespace, name, timeout_seconds, poll_seconds):
                return {"status": {"succeeded": 1}}

            def delete_config_map(self, namespace, name):
                return None

        def verify(paper, config):
            path = Path(paper.pdf_path)
            return VerificationResult(paper.title, "pass", str(path), path.stat().st_size, 10)

        with patch("literature_downloader.pipeline.KubernetesJobClient", FakeClient), patch(
            "literature_downloader.pipeline.verify_pdf", side_effect=verify
        ):
            first_result = pipeline.collect_round(first_task)

        self.assertEqual(first_result["status"], "completed")
        cached = db.get_library_paper("doi:10.1000/example")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["pdf_status"], "verified")
        self.assertTrue(Path(cached["pdf_path"]).is_file())

        second_task = pipeline.create_task("topic two", 1)
        second_id = db.upsert_paper(
            second_task,
            {"title": "Paper", "doi": "10.1000/example", "pdf_status": "pending_download"},
        )
        db.update_task(second_task, status="ready:download")
        second_result = pipeline.collect_round(second_task)

        self.assertEqual(second_result["status"], "completed")
        self.assertEqual(len(job_creations), 1)
        second_paper = db.get_paper(second_id)
        self.assertEqual(second_paper["pdf_status"], "verified")
        self.assertEqual(second_paper["pdf_path"], cached["pdf_path"])

        Path(cached["pdf_path"]).unlink()
        third_task = pipeline.create_task("topic three", 1)
        third_id = db.upsert_paper(
            third_task,
            {"title": "Paper", "doi": "10.1000/example", "pdf_status": "pending_download"},
        )
        db.update_task(third_task, status="ready:download")
        with patch("literature_downloader.pipeline.KubernetesJobClient", FakeClient):
            pipeline.collect_round(third_task)
        self.assertEqual(len(job_creations), 2)


if __name__ == "__main__":
    unittest.main()
