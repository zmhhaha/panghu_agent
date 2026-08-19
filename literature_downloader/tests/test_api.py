from __future__ import annotations

import unittest
from unittest.mock import patch

from literature_downloader import api


class ApiActionTests(unittest.TestCase):
    def test_approve_is_idempotent_after_collection_started(self) -> None:
        task = {"id": "task", "status": "running", "phase": "collect"}
        with patch.object(api, "_status_or_404", return_value=task), patch.object(api, "_start_thread") as start:
            result = api.approve_download_task("task")

        self.assertTrue(result["ok"])
        self.assertIs(result["task"], task)
        start.assert_not_called()

    def test_retry_is_idempotent_after_collection_started(self) -> None:
        task = {"id": "task", "status": "running", "phase": "collect"}
        with patch.object(api, "_status_or_404", return_value=task), patch.object(api, "_start_thread") as start:
            result = api.retry_download_task("task")

        self.assertTrue(result["ok"])
        self.assertIs(result["task"], task)
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
