"""Full local Pi scientific publication chain; synthetic fixtures, no live Pi/model."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import unittest
from unittest.mock import patch

from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_contracts import report_sha256, result_digest
from harness import IntegrationHarness


class LocalPublicationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = IntegrationHarness()
        self.addCleanup(self.h.close)

    def accepted(self, **overrides):
        response = self.h.submit(**overrides)
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()
        self.assertEqual((snapshot["engine"], snapshot["state"], snapshot["status"]), ("pi", "queued", "pending"))
        self.assertEqual(self.h.projection.get(snapshot["id"])["dispatch"], "accepted")
        return snapshot["id"]

    def publishing(self, **overrides):
        task_id = self.accepted(**overrides)
        snapshot = self.h.execute_next()
        self.assertEqual(snapshot["id"], task_id)
        self.assertEqual((snapshot["state"], snapshot["status"], snapshot["attempt"]), ("publishing", "running", 1), snapshot)
        return task_id

    def test_full_chain_validates_persists_acks_and_downloads_exact_bytes(self):
        task_id = self.accepted()
        before = self.h.public_http.get("/scientific-research/" + task_id).json()
        self.assertEqual(before["status"], "pending")
        self.assertIsNone(before["report"])
        self.assertIsNone(self.h.db.get_task(task_id), "Pi must never enter the legacy task table")
        snapshot = self.h.execute_next()
        self.assertEqual(snapshot["state"], "publishing", snapshot)
        result = self.h.worker.result(task_id)
        self.assertIs(result["validation"]["valid"], True)
        self.assertEqual(result["validation"]["validator_version"], "research-output/1.0.0")
        self.assertEqual(result_digest(result), result["result_digest"])
        self.assertEqual(report_sha256(result["report_markdown"]), result["report_sha256"])
        self.assertEqual(result["report_markdown"].encode("utf-8"), self.h.runtime.last_report_bytes)
        self.assertIsNone(self.h.db.get_report(task_id))
        self.assertEqual(self.h.public_http.get("/scientific-research/" + task_id).json()["status"], "running")

        self.h.projection.reconcile()
        done = self.h.public_http.get("/scientific-research/" + task_id)
        self.assertEqual(done.status_code, 200, done.text)
        task = done.json()
        self.assertEqual((task["state"], task["status"], task["report_id"]), ("succeeded", "done", task_id))
        self.assertEqual(task["report"], result["report_markdown"])
        self.assertIsNone(task["notification_status"])
        report = self.h.public_http.get("/scientific-reports/" + task_id).json()
        self.assertEqual(report["id"], task_id)
        self.assertEqual(report["content"], result["report_markdown"])
        self.assertIsInstance(report["keywords"], str)
        self.assertIsInstance(json.loads(report["keywords"]), list)
        search = self.h.public_http.get("/scientific-reports", params={"q": "Controlled", "limit": 10, "offset": 0})
        self.assertEqual(search.status_code, 200)
        self.assertEqual([item["id"] for item in search.json()], [task_id])
        download = self.h.public_http.get("/scientific-download/" + task_id)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, self.h.runtime.last_report_bytes)
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), result["report_sha256"])
        self.assertIn(b"\r\n", download.content)
        self.assertIn("text/markdown", download.headers["content-type"])
        self.assertEqual(self.h.transport.count("POST", "/publication-ack"), 1)
        self.assertEqual(len(self.h.runtime.calls), 1)

    def test_report_store_outage_preserves_result_then_recovers_without_research_rerun(self):
        task_id = self.publishing()
        initial_result = self.h.worker.result(task_id)
        self.h.db.fail_report_writes = True
        with self.assertLogs("scientific_agent.pi_projection", level=logging.ERROR):
            self.h.projection.reconcile()
        pending = self.h.public_http.get("/scientific-research/" + task_id).json()
        self.assertEqual((pending["state"], pending["status"], pending["error_code"]), ("publishing", "running", "storage_unavailable"))
        self.assertEqual(self.h.transport.count("POST", "/publication-ack"), 0)
        self.assertEqual(self.h.public_http.get("/scientific-download/" + task_id).status_code, 404)
        self.assertEqual(self.h.worker.result(task_id)["result_digest"], initial_result["result_digest"])
        self.h.db.fail_report_writes = False
        self.h.restart_projection().reconcile()
        self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")
        self.assertEqual(self.h.public_http.get("/scientific-download/" + task_id).content, self.h.runtime.last_report_bytes)
        self.assertEqual(len(self.h.runtime.calls), 1)
        self.assertEqual(len(self.h.db._query("SELECT * FROM scientific_reports")), 1)

    def test_cancel_after_publishing_keeps_result_and_user_admission_until_ack(self):
        task_id = self.publishing()
        result_before = self.h.worker.result(task_id)
        cancelled = self.h.public_http.post("/scientific-research/" + task_id + "/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "publishing")
        self.assertEqual(self.h.worker.result(task_id)["result_digest"], result_before["result_digest"])
        busy = self.h.submit(key="blocked-during-publication")
        self.assertEqual(busy.status_code, 429, busy.text)
        self.h.projection.reconcile()
        self.assertEqual(self.h.public_http.post("/scientific-research/" + task_id + "/cancel").json()["state"], "succeeded")
        accepted_after_ack = self.h.submit(key="new-task-after-ack")
        self.assertEqual(accepted_after_ack.status_code, 200, accepted_after_ack.text)
        self.assertEqual(accepted_after_ack.json()["state"], "queued")

    def test_lost_ack_before_commit_replays_existing_report_then_acknowledges(self):
        task_id = self.publishing()
        self.h.transport.lose_ack_before_once = True
        with self.assertLogs("scientific_agent.pi_projection", level=logging.ERROR):
            self.h.projection.reconcile()
        self.assertIsNotNone(self.h.db.get_report(task_id))
        self.assertEqual(self.h.worker.get(task_id)["state"], "publishing")
        self.assertEqual(self.h.public_http.post("/scientific-research/" + task_id + "/cancel").json()["state"], "publishing")
        self.h.restart_projection().reconcile()
        self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")
        self.assertEqual(len(self.h.db._query("SELECT * FROM scientific_reports")), 1)
        self.assertEqual(self.h.transport.count("POST", "/publication-ack"), 2)
        self.assertFalse(any("REPLACE" in query.upper() for query in self.h.db.executed))
        self.assertEqual(len(self.h.runtime.calls), 1)

    def test_lost_ack_response_after_commit_preserves_success(self):
        task_id = self.publishing()
        self.h.transport.lose_ack_after_once = True
        with self.assertLogs("scientific_agent.pi_projection", level=logging.ERROR):
            self.h.projection.reconcile()
        self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")
        self.h.restart_projection().reconcile()
        self.assertEqual(self.h.public_http.get("/scientific-research/" + task_id).json()["status"], "done")
        self.assertEqual(self.h.public_http.get("/scientific-download/" + task_id).content, self.h.runtime.last_report_bytes)
        self.assertEqual(len(self.h.db._query("SELECT * FROM scientific_reports")), 1)
        self.assertEqual(len(self.h.runtime.calls), 1)

    def test_accepted_submit_response_loss_recovers_original_task_without_second_put(self):
        self.h.transport.lose_put_response_once = True
        response = self.h.submit(key="lost-submit")
        self.assertEqual(response.status_code, 503, response.text)
        task_id = response.json()["detail"]["task_id"]
        self.assertEqual(self.h.store.get(task_id)["state"], "queued")
        self.assertEqual(self.h.projection.get(task_id)["dispatch"], "ambiguous")
        self.h.restart_projection().reconcile()
        replay = self.h.submit(key="lost-submit")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["id"], task_id)
        self.assertEqual(self.h.transport.count("PUT"), 1)
        self.h.execute_next()
        self.h.projection.reconcile()
        self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")
        self.assertEqual(len(self.h.runtime.calls), 1)

    def test_existing_foreign_report_is_never_overwritten_or_acked(self):
        task_id = self.publishing()
        with self.h.db.lock:
            self.h.db.connection.execute("INSERT INTO scientific_reports (id,task_id,content) VALUES (?,?,?)", (task_id, task_id, "foreign immutable content"))
            self.h.db.connection.commit()
        with self.assertLogs("scientific_agent.pi_projection", level=logging.ERROR):
            self.h.projection.reconcile()
        self.assertEqual(self.h.db.get_report(task_id)["content"], "foreign immutable content")
        self.assertEqual(self.h.worker.get(task_id)["state"], "publishing")
        self.assertEqual(self.h.projection.get(task_id)["error_code"], "publication_conflict")
        self.assertEqual(self.h.transport.count("POST", "/publication-ack"), 0)

    def test_invalid_citation_fails_actual_output_validation_and_cannot_publish(self):
        task_id = self.accepted()
        def mutate(workspace, fixture):
            with (workspace / "report.md").open("ab") as stream:
                stream.write(b"\nInvented reference [999].\n")
        self.h.runtime.mutate = mutate
        failed = self.h.execute_next()
        self.assertEqual((failed["state"], failed["error_code"]), ("failed", "validation_failed"), failed)
        self.assertEqual(self.h.public_http.get("/scientific-research/" + task_id).json()["status"], "failed")
        self.h.projection.reconcile()
        self.assertIsNone(self.h.db.get_report(task_id))
        self.assertEqual(self.h.transport.count("POST", "/publication-ack"), 0)
        self.assertEqual(self.h.public_http.get("/scientific-download/" + task_id).status_code, 404)

    def test_manifest_foreign_path_is_rejected_before_publication(self):
        task_id = self.accepted()
        def mutate(workspace, fixture):
            malicious = {"artifacts": [{"artifact_id": "report", "path": "../../control/queue.sqlite"}]}
            (workspace / "manifest.json").write_text(json.dumps(malicious), encoding="utf-8")
        self.h.runtime.mutate = mutate
        failed = self.h.execute_next()
        self.assertEqual((failed["state"], failed["error_code"]), ("failed", "invalid_artifact"), failed)
        self.h.projection.reconcile()
        self.assertIsNone(self.h.db.get_report(task_id))
        self.assertEqual(self.h.public_http.get("/scientific-research/" + task_id + "/artifacts").status_code, 409)

    def test_artifact_proxy_whitelist_integrity_and_private_metadata(self):
        task_id = self.publishing()
        self.h.projection.reconcile()
        result = self.h.worker.result(task_id)
        metadata = self.h.public_http.get("/scientific-research/" + task_id + "/artifacts")
        self.assertEqual(metadata.status_code, 200)
        items = metadata.json()["items"]
        self.assertTrue(items)
        for item in items:
            self.assertEqual(set(item), {"artifact_id", "media_type", "size", "sha256"})
            downloaded = self.h.public_http.get("/scientific-research/" + task_id + "/artifacts/" + item["artifact_id"])
            self.assertEqual(downloaded.status_code, 200, downloaded.text)
            self.assertEqual(len(downloaded.content), item["size"])
            self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), item["sha256"])
            self.assertEqual(downloaded.headers["x-content-type-options"], "nosniff")
            self.assertNotIn("authorization", downloaded.headers)
        artifact_report = self.h.public_http.get("/scientific-research/" + task_id + "/artifacts/report")
        self.assertEqual(artifact_report.content, result["report_markdown"].encode("utf-8"))
        self.assertNotIn("provenance", {item["artifact_id"] for item in items})
        for private_id in ("queue", "provenance", "session", "environment"):
            self.assertEqual(self.h.public_http.get("/scientific-research/" + task_id + "/artifacts/" + private_id).status_code, 404)
        bad = self.h.public_http.get("/scientific-research/" + task_id + "/artifacts/bad.name")
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn(self.h.TOKEN, metadata.text)
        self.assertNotIn(str(self.h.root), metadata.text)

    def test_queued_cancel_never_runs_and_worker_unavailable_is_not_legacy_404(self):
        task_id = self.accepted()
        cancelled = self.h.public_http.post("/scientific-research/" + task_id + "/cancel")
        self.assertEqual((cancelled.json()["state"], cancelled.json()["status"]), ("cancelled", "failed"))
        self.assertIsNone(self.h.store.claim())
        self.assertEqual(len(self.h.runtime.calls), 0)
        self.h.transport.unavailable = True
        unavailable = self.h.public_http.get("/scientific-research/" + task_id)
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["detail"]["code"], "worker_unavailable")

    def test_durable_rejection_prevents_execution_after_index_update_loss_and_capacity_recovers(self):
        first_id = self.accepted(key="occupies-user-slot")
        self.h.db.fail_rejection_update_once = True
        rejected = self.h.submit(key="durably-rejected-id")
        self.assertEqual(rejected.status_code, 503, rejected.text)
        rejected_id = rejected.json()["detail"]["task_id"]
        self.assertEqual(self.h.projection.get(rejected_id)["dispatch"], "prepared")
        rejected_job = json.loads(self.h.projection.get(rejected_id)["job"])
        self.assertEqual(self.h.public_http.post("/scientific-research/" + first_id + "/cancel").json()["state"], "cancelled")
        puts_before_recovery = self.h.transport.count("PUT")
        with self.assertLogs("scientific_agent.pi_projection", level=logging.ERROR):
            self.h.restart_projection().reconcile()
        self.assertEqual(self.h.projection.get(rejected_id)["dispatch"], "rejected")
        self.assertEqual(self.h.transport.count("PUT"), puts_before_recovery)
        for operation in (lambda: self.h.worker.get(rejected_id), lambda: self.h.worker.put(rejected_job)):
            with self.assertRaises(WorkerError) as raised:
                operation()
            self.assertEqual((raised.exception.status, raised.exception.code), (429, "user_busy"))
        replay = self.h.submit(key="durably-rejected-id")
        self.assertEqual(replay.status_code, 429, replay.text)
        self.assertIsNone(self.h.store.claim(), "the durably rejected ID must never be queued")
        self.assertEqual(len(self.h.runtime.calls), 0)
        self.assertEqual(self.h.submit(key="new-id-after-rejection").status_code, 200)

    def test_running_cancel_propagates_signal_and_no_result_is_published(self):
        task_id = self.accepted()
        entered = threading.Event()
        original_execute = self.h.runtime.execute
        def blocked(job, workspace, event_sink, cancel_signal):
            original_execute(job, workspace, event_sink, cancel_signal)
            entered.set()
            if not cancel_signal.wait(5):
                raise AssertionError("cancellation was not delivered")
        self.h.runtime.execute = blocked
        errors = []
        def execute():
            try:
                self.h.execute_next()
            except BaseException as exc:
                errors.append(exc)
        runner = threading.Thread(target=execute, daemon=True)
        runner.start()
        try:
            self.assertTrue(entered.wait(3), "fixture runtime did not start")
            cancelled = self.h.public_http.post("/scientific-research/" + task_id + "/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertEqual(cancelled.json()["state"], "cancel_requested")
            runner.join(3)
            self.assertFalse(runner.is_alive(), "scheduler did not finish cancellation")
            self.assertEqual(errors, [])
            terminal = self.h.public_http.get("/scientific-research/" + task_id).json()
            self.assertEqual((terminal["state"], terminal["status"], terminal["error_code"]), ("cancelled", "failed", "cancelled"))
            self.h.projection.reconcile()
            self.assertIsNone(self.h.db.get_report(task_id))
            self.assertEqual(self.h.transport.count("POST", "/publication-ack"), 0)
        finally:
            self.h.scheduler.stop_event.set()
            if self.h.scheduler.active_cancel:
                self.h.scheduler.active_cancel.set()
            runner.join(3)

    def test_acquired_full_text_is_validated_and_hashed_but_not_publicly_downloadable(self):
        task_id = self.accepted()
        full_text = b"Synthetic acquired full-text fixture. Section Findings: controlled result.\n"
        def mutate(workspace, fixture):
            paper = fixture["papers"][0]
            paper["availability"]["full_text"] = {"available": True, "obtained_at": paper["retrieved_at"]}
            paper["full_text_path"] = "texts/fixture.txt"
            (workspace / "texts").mkdir()
            (workspace / "texts" / "fixture.txt").write_bytes(full_text)
            (workspace / "papers.jsonl").write_text(json.dumps(paper) + "\n", encoding="utf-8")
            evidence = fixture["evidence"][0]
            evidence.update(access_level="full_text", locator="Findings section, paragraph 1")
            (workspace / "evidence.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        self.h.runtime.mutate = mutate
        published = self.h.execute_next()
        self.assertEqual(published["state"], "publishing", published)
        result = self.h.worker.result(task_id)
        self.assertIs(result["validation"]["valid"], True)
        self.assertEqual(result["provenance"]["acquired_full_text"], [{"path": "texts/fixture.txt", "size": len(full_text), "sha256": hashlib.sha256(full_text).hexdigest()}])
        self.h.projection.reconcile()
        self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")
        private_result, private_root = self.h.store.artifact_record(task_id)
        self.assertEqual((private_root / "texts" / "fixture.txt").read_bytes(), full_text)
        public_items = self.h.public_http.get("/scientific-research/" + task_id + "/artifacts").json()["items"]
        self.assertEqual({item["artifact_id"] for item in public_items}, {item["artifact_id"] for item in private_result["artifacts"]})
        self.assertNotIn("texts", {item["artifact_id"] for item in public_items})
        self.assertEqual(self.h.public_http.get("/scientific-research/" + task_id + "/artifacts/fixture").status_code, 404)
        self.assertEqual(result_digest(result), result["result_digest"])

    def test_publishing_is_not_timed_out_after_execution_deadline(self):
        task_id = self.publishing()
        result_before = self.h.worker.result(task_id)
        with patch("pi_worker.store.utc_now", return_value="2099-01-01T00:00:00Z"):
            self.h.store.expire_queued()
            self.assertEqual(self.h.worker.cancel(task_id)["state"], "publishing")
            self.h.projection.reconcile()
            self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")
        self.assertEqual(self.h.worker.result(task_id)["result_digest"], result_before["result_digest"])
        self.assertEqual(len(self.h.runtime.calls), 1)

    def test_foreign_hardlinked_report_is_rejected_by_actual_controller(self):
        task_id = self.accepted()
        outside = self.h.root / "foreign-report.md"
        outside.write_bytes(b"foreign content must not be published")
        def mutate(workspace, fixture):
            report = workspace / "report.md"
            report.unlink()
            os.link(outside, report)
        self.h.runtime.mutate = mutate
        failed = self.h.execute_next()
        self.assertEqual((failed["state"], failed["error_code"]), ("failed", "invalid_artifact"), failed)
        self.h.projection.reconcile()
        self.assertIsNone(self.h.db.get_report(task_id))
        self.assertEqual(outside.read_bytes(), b"foreign content must not be published")

    def test_corrupt_controller_artifact_is_rejected_without_changing_published_report(self):
        task_id = self.publishing()
        self.h.projection.reconcile()
        result, result_path = self.h.store.artifact_record(task_id)
        report_path = result_path / "report.md"
        report_path.chmod(0o600)
        report_path.write_bytes(b"controlled artifact corruption")
        corrupt = self.h.public_http.get("/scientific-research/" + task_id + "/artifacts/report")
        self.assertEqual(corrupt.status_code, 409, corrupt.text)
        self.assertEqual(corrupt.json()["detail"]["code"], "artifact_corrupt")
        published = self.h.public_http.get("/scientific-download/" + task_id)
        self.assertEqual(published.content, self.h.runtime.last_report_bytes)
        self.assertEqual(hashlib.sha256(published.content).hexdigest(), result["report_sha256"])

    def test_worker_authentication_and_stale_publication_ack_fail_closed(self):
        self.assertEqual(self.h.worker_http.get("/v1/health").status_code, 401)
        self.assertEqual(self.h.worker_http.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        task_id = self.publishing()
        result = self.h.worker.result(task_id)
        with self.assertRaises(WorkerError) as raised:
            self.h.worker.ack(task_id, {"attempt": result["attempt"] + 1, "result_digest": result["result_digest"], "report_id": task_id})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.h.worker.get(task_id)["state"], "publishing")
        self.h.projection.reconcile()
        self.assertEqual(self.h.worker.get(task_id)["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
