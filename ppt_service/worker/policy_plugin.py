"""Hermes tool policy, not a shell security boundary.

Shell commands can fork or use networks. Pod stop-write verification remains
required. The ready marker is only a startup check, writable by the same UID.
api_request_id is Hermes correlation metadata, not a provider request ID.
No request, response, prompt, key or raw provider error is inspected or stored.
Deduplication belongs to the business consumer. Current hooks supply no price.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

POLICY_VERSION = 1
ALLOWED_TOOLS = frozenset({
    "terminal", "read_file", "write_file", "patch", "search_files",
    "skills_list", "skill_view", "todo_list", "vision_analyze",
})
_TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "prompt_tokens", "total_tokens", "request_count",
)
_ID_FIELDS = (
    "task_id", "turn_id", "api_request_id", "session_id", "model", "provider",
    "api_mode", "response_model",
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,200}\Z")


def _block(message):
    return {"action": "block", "message": message}


def _nonnegative_number(value):
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float and math.isfinite(value) and value >= 0:
        return value
    return None


def _identifier(value):
    return value if type(value) is str and _IDENTIFIER.fullmatch(value) else None


def pre_tool_call(tool_name=None, args=None, **kwargs):
    """Default timeout None/omitted is valid; worker default must be <=600s."""
    try:
        if type(tool_name) is not str or tool_name not in ALLOWED_TOOLS:
            return _block("PPT policy: tool is not allowed")
        if type(args) is not dict:
            return _block("PPT policy: invalid tool arguments")
        if tool_name == "vision_analyze":
            image = args.get("image_url")
            if type(image) is not str or not image.startswith(("/work/inputs/", "/work/project/")):
                return _block("PPT policy: vision requires a current-task local image")
            resolved = Path(image).resolve()
            if not any(resolved.is_relative_to(root.resolve()) for root in (Path("/work/inputs"), Path("/work/project"))):
                return _block("PPT policy: vision image leaves the current task")
        if tool_name == "terminal":
            if args.get("background", False) is not False:
                return _block("PPT policy: background terminal is not allowed")
            if args.get("pty", False) is not False:
                return _block("PPT policy: terminal PTY is not allowed")
            timeout = args.get("timeout")
            if timeout is not None:
                checked = _nonnegative_number(timeout)
                if checked is None or not 0 < checked <= 600:
                    return _block("PPT policy: terminal timeout must be > 0 and <= 600")
        return None
    except Exception:
        return _block("PPT policy: decision failed closed")


def _usage_row(event, payload):
    usage = payload.get("usage")
    usage = usage if type(usage) is dict else {}
    row = {"event": event}
    row.update({key: _identifier(payload.get(key)) for key in _ID_FIELDS})
    row["usage"] = {key: _nonnegative_number(usage.get(key)) for key in _TOKEN_FIELDS}
    row["cost_usd"] = _nonnegative_number(usage.get("cost_usd"))
    return row


def _write_ready(result_dir):
    result_dir.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=result_dir,
                                         prefix=".policy-ready-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"policy_version": POLICY_VERSION, "pid": os.getpid()}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, result_dir / "policy-ready.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def register(ctx):
    """Launcher must remove old markers and verify this process's PID/version."""
    result_dir = Path(os.environ.get("PPT_RESULT_DIR", "/work/result"))
    write_lock = threading.Lock()

    def append_usage(event, payload):
        line = json.dumps(_usage_row(event, payload), allow_nan=False, ensure_ascii=True, separators=(",", ":")) + "\n"
        try:
            with write_lock:
                result_dir.mkdir(parents=True, exist_ok=True)
                with (result_dir / "usage.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(line)
        except OSError:
            raise RuntimeError("PPT usage audit write failed") from None

    def post_api_request(**kwargs):
        append_usage("post_api_request", kwargs)

    def api_request_error(**kwargs):
        append_usage("api_request_error", kwargs)

    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_api_request", post_api_request)
    ctx.register_hook("api_request_error", api_request_error)
    _write_ready(result_dir)
