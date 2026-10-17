"""Model selection stays bound to the request through execution and cache lookup."""
from __future__ import annotations

import collections
import importlib
import os
import threading
import unittest
from unittest.mock import Mock, patch

from baijiazhengming.registry import get_registry
from baijiazhengming.runtime import AgentRuntime
from baijiazhengming.task_store import TaskStore


class ModelRoutingTest(unittest.TestCase):
    def setUp(self):
        self.definition = get_registry().get("daofaziran")

    def test_public_and_internal_entries_select_different_sources(self):
        with patch.object(AgentRuntime, "__init__", return_value=None):
            api = importlib.import_module("baijiazhengming.api")
        request = api.SubmitRequest(text="test")
        submission = Mock(task_id="id", text="test", status="pending", report=None, cached=False)
        fake_runtime = Mock()
        fake_runtime.submit.return_value = submission
        with patch.object(api, "runtime", fake_runtime):
            api.submit_task(self.definition.slug, request)
            api.submit_internal_task(self.definition.slug, request)
            api._legacy_submit(self.definition)(request)
        self.assertEqual(fake_runtime.submit.call_args_list[0].kwargs["model"], None)
        self.assertEqual(fake_runtime.submit.call_args_list[1].kwargs["model"], self.definition.llm_model)
        self.assertEqual(fake_runtime.submit.call_args_list[2].kwargs["model"], self.definition.llm_model)

    def test_runtime_carries_model_to_plugin_and_cache(self):
        runtime = AgentRuntime.__new__(AgentRuntime)
        store = Mock()
        store.get_running_task.return_value = None
        store.find_cached.return_value = None
        store.create_task.return_value = "id"
        plugin = Mock()
        plugin.configuration_error.return_value = None
        plugin.run.return_value.content = "answer"
        runtime._stores = {self.definition.slug: store}
        runtime._plugins = {self.definition.slug: plugin}
        runtime._queues = {self.definition.slug: collections.deque()}
        runtime._queue_lock = threading.Lock()
        runtime._queue_event = threading.Event()
        slot = Mock()
        with patch.dict(os.environ, {"LLM_MODEL": "custom-guarded"}):
            runtime.submit(self.definition, "public")
        runtime.submit(self.definition, "internal", model="custom-trusted")
        queued = runtime._queues[self.definition.slug]
        self.assertEqual([item.model for item in queued], ["custom-guarded", "custom-trusted"])
        self.assertEqual(store.find_cached.call_args_list[0].args, ("public", "custom-guarded"))
        self.assertEqual(store.find_cached.call_args_list[1].args, ("internal", "custom-trusted"))
        with patch("baijiazhengming.runtime.get_reference", return_value="reference"):
            runtime._run(self.definition, "id", "internal", "custom-trusted", slot)
        plugin.run.assert_called_once_with("internal", "reference", "custom-trusted")
        store.save_report.assert_called_once_with("id", "internal", "answer", "custom-trusted")

    def test_cache_query_includes_model(self):
        store = TaskStore("daofaziran_agent")
        with patch("baijiazhengming.task_store.sqlite_client._query", return_value=[]) as query:
            store.find_cached("same text", "custom-guarded")
        self.assertIn("model_used='custom-guarded'", query.call_args.args[0])

    def test_stable_alias_never_reads_old_report_cache(self):
        store = TaskStore("daofaziran_agent")
        with patch("baijiazhengming.task_store.sqlite_client._query") as query:
            for model in ("llm-guarded", "llm-trusted"):
                self.assertIsNone(store.find_cached("same text", model))
            query.assert_not_called()

    def test_report_schema_migration_and_nullable_revision(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE daofaziran_agent_reports (id TEXT PRIMARY KEY, task_id TEXT, topic TEXT, summary TEXT, keywords TEXT, content TEXT, model_used TEXT, created_at TEXT)')
        def query(sql): return [dict(row) for row in db.execute(sql)]
        with patch('baijiazhengming.task_store.sqlite_client._query', side_effect=query), patch('baijiazhengming.task_store.sqlite_client._execute', side_effect=db.execute):
            store = TaskStore('daofaziran_agent')
            store.initialize()
            store.initialize()
            store.save_report('id', 'topic', 'answer', 'llm-guarded')
            row = query('SELECT * FROM daofaziran_agent_reports')[0]
            self.assertEqual(row['requested_alias'], 'llm-guarded')
            self.assertIsNone(row['route_revision'])
        db.close()

    def test_each_crew_builds_requested_model(self):
        for definition in get_registry().list_enabled():
            with self.subTest(agent=definition.slug):
                module = importlib.import_module(
                    f"baijiazhengming.personas.{definition.slug}_agent.crew"
                )
                with patch.object(module, "_LLM_BASE_URL", "http://llm.test/v1"), patch.dict(
                    os.environ, {f"LLM_TOKEN_{definition.slug.upper()}": "test-token"}
                ), patch.object(module, "LLM") as llm:
                    module.build_model("custom-trusted")
                self.assertEqual(llm.call_args.kwargs["model"], "custom-trusted")


if __name__ == "__main__":
    unittest.main()
