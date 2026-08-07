from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.custom_tools import WebFetchTool, _resolve_agent_path, _validate_public_url


class FileBoundaryTests(unittest.TestCase):
    def test_agent_path_stays_inside_configured_root(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"AGENT_FILE_ROOT": directory}
        ):
            resolved = _resolve_agent_path("reports/result.md")

        self.assertEqual(resolved, Path(directory).resolve() / "reports" / "result.md")

    def test_agent_path_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"AGENT_FILE_ROOT": directory}
        ):
            with self.assertRaises(ValueError):
                _resolve_agent_path("../outside.txt")


class WebFetchSecurityTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            _validate_public_url("file:///etc/passwd")

    def test_rejects_loopback_address(self):
        with self.assertRaises(ValueError):
            _validate_public_url("http://127.0.0.1:8000/private")

    def test_rejects_redirect_to_private_address(self):
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "http://127.0.0.1/admin"}
        redirect.close.return_value = None

        public_dns = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        with patch("tools.custom_tools.socket.getaddrinfo", return_value=public_dns), patch(
            "requests.get", return_value=redirect
        ) as request_get, patch("tools.custom_tools.time.sleep", return_value=None):
            output = WebFetchTool()._run("https://public.example/start")

        self.assertIn("安全策略", output)
        self.assertEqual(request_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
