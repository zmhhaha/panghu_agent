import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from ppt_service.worker import policy_plugin as policy


class Context:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        if name in self.hooks:
            raise ValueError("duplicate hook")
        self.hooks[name] = callback


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"PPT_RESULT_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ctx = Context()
        policy.register(self.ctx)

    def rows(self):
        return [json.loads(line) for line in (self.root / "usage.jsonl").read_text().splitlines()]

    def blocked(self, name, args):
        self.assertEqual(self.ctx.hooks["pre_tool_call"](tool_name=name, args=args)["action"], "block")

    def test_ready_marker_matches_gateway_and_hooks(self):
        marker = json.loads((self.root / "policy-ready.json").read_text())
        self.assertEqual(marker, {"policy_version": 1, "pid": os.getpid()})
        self.assertEqual(set(self.ctx.hooks), {"pre_tool_call", "post_api_request", "api_request_error"})

    def test_tool_default_deny_and_valid_foreground(self):
        for name in ("skill_manage", "process_manage", "delegate_task", "cronjob", "web_fetch", "browser", "future_tool", None):
            self.blocked(name, {})
        for args in ({}, {"timeout": None}, {"timeout": 600}, {"background": False, "pty": False}):
            self.assertIsNone(policy.pre_tool_call("terminal", args))
        # This is a tool policy, not a shell parser or isolation boundary.
        self.assertIsNone(policy.pre_tool_call("terminal", {"command": "sleep 1000 &"}))

    def test_invalid_background_pty_timeout_and_args_block(self):
        for name in ("background", "pty"):
            for value in (True, None, "false", 0, 1, []):
                self.blocked("terminal", {name: value})
        for value in (0, -1, 601, True, "600", float("nan"), float("inf")):
            self.blocked("terminal", {"timeout": value})
        for value in (None, [], "{}", 0):
            self.blocked("read_file", value)

    def test_vision_only_current_task_local_files(self):
        self.assertIsNone(policy.pre_tool_call("vision_analyze", {"image_url": "/work/project/deck/.preview/01.png"}))
        for image in ("https://example.com/a.png", "/work/home/key.png", "/work/project/../../etc/passwd", "file:///work/project/a.png", None):
            self.blocked("vision_analyze", {"image_url": image})

    def test_unexpected_policy_exception_fails_closed(self):
        with patch.object(policy, "_nonnegative_number", side_effect=RuntimeError("private-command")):
            result = policy.pre_tool_call("terminal", {"timeout": 1})
        self.assertEqual(result["action"], "block")
        self.assertNotIn("private-command", result["message"])

    def test_success_telemetry_is_whitelisted_and_cost_unknown(self):
        self.ctx.hooks["post_api_request"](api_request_id="hermes_1", session_id="session1", usage={"input_tokens": 10, "output_tokens": 2, "raw_usage": "secret"}, request={"prompt": "private-prompt"}, response={"text": "private-output"}, error="private-provider-error", api_key="private-key", base_url="https://private.example")
        row = self.rows()[0]
        self.assertEqual(row["usage"]["input_tokens"], 10)
        self.assertEqual(row["usage"]["output_tokens"], 2)
        self.assertIsNone(row["cost_usd"])
        serialized = json.dumps(row)
        for forbidden in ("secret", "private-", "private.example", "raw_usage"):
            self.assertNotIn(forbidden, serialized)

    def test_errors_and_malformed_values_are_unknown_not_zero(self):
        self.ctx.hooks["api_request_error"](api_request_id="error1", error={"message": "private"}, request={"body": "private"})
        row = self.rows()[0]
        self.assertTrue(all(value is None for value in row["usage"].values()))
        self.assertIsNone(row["cost_usd"])
        for value in (-1, True, "2", float("nan"), float("inf"), {}, []):
            self.ctx.hooks["post_api_request"](usage={"input_tokens": value, "cost_usd": value})
            self.assertIsNone(self.rows()[-1]["usage"]["input_tokens"])
            self.assertIsNone(self.rows()[-1]["cost_usd"])

    def test_observers_do_not_stringify_private_objects(self):
        class Private:
            def __str__(self):
                raise AssertionError("must not stringify")
        self.ctx.hooks["post_api_request"](usage=Private(), request=Private(), response=Private(), error=Private(), api_request_id="id\nsecret")
        self.assertIsNone(self.rows()[0]["api_request_id"])

    def test_concurrent_and_duplicate_rows_are_complete(self):
        callback = self.ctx.hooks["post_api_request"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: callback(api_request_id=f"request_{n}", usage={"input_tokens": n}), range(30)))
        callback(api_request_id="request_1", usage={"input_tokens": 1})
        self.assertEqual(len(self.rows()), 31)
        self.assertEqual(sum(row["api_request_id"] == "request_1" for row in self.rows()), 2)

    def test_registration_and_marker_failures_do_not_admit(self):
        marker = self.root / "policy-ready.json"
        marker.unlink()
        with patch.object(self.ctx, "register_hook", side_effect=RuntimeError("register failed")):
            with self.assertRaises(RuntimeError):
                policy.register(self.ctx)
        self.assertFalse(marker.exists())
        with patch.object(policy.os, "replace", side_effect=OSError("marker failed")):
            with self.assertRaises(OSError):
                policy.register(Context())
        self.assertFalse(marker.exists())
        self.assertEqual(list(self.root.glob(".policy-ready-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
