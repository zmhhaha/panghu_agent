"""Concurrent legacy/Pi route behavior with local SQLite and the real Pi worker.

CrewAI's model execution alone is replaced by a bounded, blocked fixture. Pi
uses the existing synthetic research runtime and normal worker/publication
pipeline. No network, model credentials or mail are required.
"""
from contextlib import ExitStack
import threading
import types
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.scientific_agent import create_app
from pi_worker.research_tools import BUNDLE_REVISION
from scientific_agent.pi_projection import PiProjection
from harness import IntegrationHarness, SharedSQLFacade


class MixedSQLFacade(SharedSQLFacade):
    """Add the existing legacy helper interface to the shared SQL fixture."""

    def create_task(self, topic, user_id):
        task_id = str(uuid.uuid4())
        with self.lock:
            self.connection.execute(
                "INSERT INTO scientific_tasks(id,topic,status,user_id) VALUES(?,?,'pending',?)",
                (task_id, topic, user_id))
            self.connection.commit()
        return task_id

    def update_task(self, task_id, **values):
        if not values or set(values) - {"status", "report", "error"}:
            raise AssertionError("Unexpected legacy fixture update")
        with self.lock:
            self.connection.execute(
                "UPDATE scientific_tasks SET " + ",".join(field + "=?" for field in values) + " WHERE id=?",
                (*values.values(), task_id))
            self.connection.commit()

    def save_report(self, task_id, topic, summary, keywords, content):
        with self.lock:
            self.connection.execute(
                "INSERT INTO scientific_reports(id,task_id,topic,summary,keywords,content) VALUES(?,?,?,?,?,?)",
                (task_id, task_id, topic, summary, keywords, content))
            self.connection.commit()


class MixedEngineConcurrencyTests(unittest.TestCase):
    def exercise_order(self, first_engine):
        legacy_started, legacy_release = threading.Event(), threading.Event()
        legacy_threads = []
        legacy_report = "Controlled legacy fixture result; no model or network calls."

        def kickoff(*, inputs):
            self.assertEqual(inputs, {"topic": "Legacy mixed-engine fixture"})
            legacy_threads.append(threading.current_thread())
            legacy_started.set()
            if not legacy_release.wait(15):
                raise AssertionError("Legacy fixture was not released within its bound")
            return legacy_report

        crew_module = types.ModuleType("scientific_agent.crew")
        crew_module.create_scientific_crew = lambda: types.SimpleNamespace(kickoff=kickoff)

        with ExitStack() as stack:
            harness = IntegrationHarness()
            stack.callback(harness.close)
            shared = MixedSQLFacade()
            stack.callback(shared.connection.close)
            projection = PiProjection(shared, harness.worker, BUNDLE_REVISION,
                                      harness.RUNTIME_REVISION,
                                      notifier=lambda *args: self.fail("Mixed-engine acceptance must not send mail"))
            client = stack.enter_context(TestClient(create_app(shared, harness.worker, projection, start_reconciler=False)))
            stack.enter_context(patch.dict("sys.modules", {"scientific_agent.crew": crew_module}))
            stack.enter_context(patch.dict("os.environ", {
                "SCIENTIFIC_DEFAULT_ENGINE": "crewai", "LLM_BASE_URL": "http://fixture.invalid",
                "LLM_SERVICE_TOKEN": "synthetic-not-a-secret"}))
            user = "trusted-mixed-engine-fixture"

            def submit(engine, key):
                payload = dict(topic="Legacy mixed-engine fixture" if engine == "crewai" else "Controlled fixture research",
                               user_id=user, email="", mode="brief", timeout_seconds=1800)
                # CrewAI selects the configured default; Pi is explicitly chosen.
                if engine == "pi":
                    payload["engine"] = "pi"
                return client.post("/scientific-research", json=payload, headers={"Idempotency-Key": key})

            def task(task_id):
                response = client.get("/scientific-research/" + task_id)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            try:
                accepted = {}
                for engine in (first_engine, "pi" if first_engine == "crewai" else "crewai"):
                    response = submit(engine, "first-" + engine)
                    self.assertEqual(response.status_code, 200, response.text)
                    accepted[engine] = response.json()["id"]
                    self.assertEqual(response.json()["engine"], engine)
                    if engine == "crewai":
                        self.assertTrue(legacy_started.wait(3), "Legacy background execution did not start")

                legacy_id, pi_id = accepted["crewai"], accepted["pi"]
                self.assertNotEqual(legacy_id, pi_id)
                self.assertEqual(task(legacy_id)["status"], "running")
                self.assertEqual(task(pi_id)["state"], "queued")
                self.assertIsNone(shared.get_task(pi_id), "Pi must not consume the legacy task row")
                self.assertIsNone(projection.get(legacy_id), "Legacy must not consume the Pi index")

                # One active job per engine for the same user. Both paths enforce
                # their own admission even while the other engine remains active.
                self.assertEqual(submit("pi", "second-pi").status_code, 429)
                calls = harness.transport.count("PUT")
                self.assertEqual(submit("crewai", "second-legacy").status_code, 429)
                self.assertEqual(harness.transport.count("PUT"), calls)
                with patch.dict("os.environ", {"SCIENTIFIC_DEFAULT_ENGINE": "pi"}):
                    denied = client.post("/scientific-research", json={
                        "topic": "Default switches only future submissions", "user_id": user,
                        "email": "", "mode": "brief"}, headers={"Idempotency-Key": "default-now-pi"})
                    self.assertEqual(denied.status_code, 429)
                    self.assertEqual((task(legacy_id)["engine"], task(legacy_id)["status"]), ("crewai", "running"))
                    self.assertEqual((task(pi_id)["engine"], task(pi_id)["state"]), ("pi", "queued"))

                # Legacy cancellation is unsupported and cannot affect Pi. Pi
                # cancellation releases only Pi admission; CrewAI stays running.
                self.assertEqual(client.post("/scientific-research/" + legacy_id + "/cancel").status_code, 409)
                self.assertEqual(task(pi_id)["state"], "queued")
                cancelled = client.post("/scientific-research/" + pi_id + "/cancel")
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()["state"], "cancelled")
                self.assertEqual(task(legacy_id)["status"], "running")

                next_pi = submit("pi", "pi-after-cancel")
                self.assertEqual(next_pi.status_code, 200, next_pi.text)
                next_pi_id = next_pi.json()["id"]
                self.assertEqual(submit("crewai", "legacy-still-busy").status_code, 429)
                published = harness.execute_next()
                self.assertEqual((published["id"], published["state"]), (next_pi_id, "publishing"))
                projection.reconcile()
                self.assertEqual(task(next_pi_id)["state"], "succeeded")
                self.assertEqual(task(legacy_id)["status"], "running")
                self.assertIsNone(task(next_pi_id)["notification_status"])

                legacy_release.set()
                for thread in legacy_threads:
                    thread.join(3)
                    self.assertFalse(thread.is_alive(), "Legacy fixture did not finish")
                self.assertEqual((task(legacy_id)["engine"], task(legacy_id)["status"], task(legacy_id)["report"]),
                                 ("crewai", "done", legacy_report))
                self.assertEqual(task(pi_id)["state"], "cancelled")
                self.assertEqual(task(next_pi_id)["state"], "succeeded")
                self.assertEqual(client.get("/scientific-download/" + legacy_id).content, legacy_report.encode())
                self.assertEqual(client.get("/scientific-download/" + next_pi_id).content, harness.runtime.last_report_bytes)
                self.assertEqual(shared._query("SELECT count(*) AS n FROM scientific_reports")[0]["n"], 2)
                self.assertEqual(shared._query("SELECT * FROM scientific_notifications"), [])
            finally:
                legacy_release.set()
                for thread in legacy_threads:
                    thread.join(3)

    def test_active_crewai_allows_pi_with_independent_admission_cancel_and_defaults(self):
        self.exercise_order("crewai")

    def test_active_pi_allows_crewai_with_independent_admission_cancel_and_defaults(self):
        self.exercise_order("pi")


if __name__ == "__main__":
    unittest.main()
