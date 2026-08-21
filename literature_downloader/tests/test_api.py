from __future__ import annotations

import unittest
from unittest.mock import patch

from literature_downloader import api


class ApiActionTests(unittest.TestCase):
    def test_same_owner_can_create_independent_tasks(self) -> None:
        first = {"id": "task-1", "status": "running", "phase": "search"}
        second = {"id": "task-2", "status": "running", "phase": "search"}
        with patch.object(api.pipeline, "create_task", side_effect=["task-1", "task-2"]), patch.object(
            api, "_start_thread"
        ) as start, patch.object(api, "_status_or_404", side_effect=[first, second]):
            result1 = api.create_download_task(
                api.DownloadRequest(topic="first", email="same@example.com", user_id="same-user")
            )
            result2 = api.create_download_task(
                api.DownloadRequest(topic="second", email="same@example.com", user_id="same-user")
            )

        self.assertEqual(result1["id"], "task-1")
        self.assertEqual(result2["id"], "task-2")
        self.assertEqual(start.call_count, 2)

    def test_download_starts_only_after_search_is_ready(self) -> None:
        task = {"id": "task", "status": "ready:download", "phase": "search"}
        with patch.object(api, "_status_or_404", return_value=task), patch.object(api, "_start_thread") as start:
            result = api.start_download_task("task")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "download")
        start.assert_called_once_with("task", "collect")


if __name__ == "__main__":
    unittest.main()
