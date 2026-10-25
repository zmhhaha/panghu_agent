"""Single-attempt, foreground Hermes gateway lifecycle.

The process produces candidate artifacts only. The trusted dispatcher must wait
for the whole container to exit, freeze files and independently validate them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid

from .hermes_client import HermesClient, HermesError, safe_id
from .prompts import PHASES, input_message, stage_instructions
from .preview import OfficialPreview

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# Keep aligned with candidate_validation.validate_manifest. This is only a
# handoff check: the stopped writer's snapshot still needs independent validation.
CANDIDATE_STATES = {
    "plan_initial": {"awaiting_confirmation", "needs_input"},
    "plan_options": {"awaiting_confirmation", "needs_input"},
    "prepare": {"prepared", "needs_input"},
    "generate": {"candidate", "needs_input"},
    "revise": {"candidate", "needs_input"},
}


def _hermes_failure(error: HermesError) -> dict:
    """Private fixed categories only; never retain an exception URL or body."""
    operation = error.operation
    category = {
        '/v1/capabilities': 'capabilities',
        '/api/sessions': 'create_session',
        '/v1/runs': 'admit',
        'response_too_large': 'response_validation',
        'invalid_json': 'response_validation',
        'event_too_large': 'response_validation',
        'invalid_event': 'response_validation',
    }.get(operation, 'unknown') if type(operation) is str else 'unknown'
    if type(operation) is str:
        identifier = r'[A-Za-z0-9_.-]{1,256}'
        for pattern, name in ((rf'/v1/runs/{identifier}', 'status'),
                              (rf'/v1/runs/{identifier}/stop', 'stop'),
                              (rf'/v1/runs/{identifier}/events', 'events'),
                              (rf'/api/sessions/{identifier}', 'get_session')):
            if re.fullmatch(pattern, operation):
                category = name
                break
    status = error.status
    return {'hermes_operation': category,
            'hermes_http_status': status if type(status) is int and 100 <= status <= 599 else None}


def candidate_handoff(path: Path, task: dict) -> str:
    """Check only bounded JSON identity/state, never artifact hashes or approval.

    This runs while the gateway exists and cannot prove a quiescent snapshot.
    The caller must still stop the process tree before independent validation.
    """
    for component in (*reversed(path.absolute().parents), path.absolute()):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Candidate manifest path contains a link")
        if component != path.absolute() and not stat.S_ISDIR(info.st_mode):
            raise ValueError("Candidate manifest parent is not a directory")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Candidate manifest must be an ordinary file")
    if info.st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Candidate manifest exceeds size limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not os.path.samestat(info, opened) or opened.st_size > MAX_MANIFEST_BYTES):
            raise ValueError("Candidate manifest changed or is not an ordinary file")
        data = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES or len(data) != opened.st_size:
        raise ValueError("Candidate manifest size changed or exceeds limit")

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate candidate JSON key")
            value[key] = item
        return value

    def constant(value):
        raise ValueError("Invalid candidate JSON constant")

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Non-finite candidate JSON number")
        return parsed

    manifest = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=constant, parse_float=number)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1):
        raise ValueError("Expected candidate manifest schema_version 1")
    if manifest.get("phase") != task["phase"]:
        raise ValueError("Candidate manifest phase mismatch")
    if manifest.get("input_snapshot_sha256") != task["input_snapshot_sha256"]:
        raise ValueError("Candidate manifest input snapshot mismatch")
    status = manifest.get("status")
    if not isinstance(status, str) or status not in CANDIDATE_STATES[task["phase"]]:
        raise ValueError("Invalid candidate manifest phase/status pair")
    return status


def atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".new")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def read_task(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("Invalid task document")
    task = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(task, dict) or task.get("phase") not in PHASES:
        raise ValueError("Invalid phase")
    for field in ("job_id", "session_id"):
        safe_id(task.get(field, ""))
    if type(task.get("attempt")) is not int or not 1 <= task["attempt"] <= 100:
        raise ValueError("Invalid attempt")
    digest = task.get("input_snapshot_sha256", "")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Missing input snapshot hash")
    if task["phase"] in {"plan_options", "prepare"}:
        confirmation = "stage1_confirmation" if task["phase"] == "plan_options" else "stage2_confirmation"
        if not isinstance(task.get(confirmation), dict) or not task[confirmation]:
            raise ValueError("Explicit stage confirmation is required")
    requirements = task.get("requirements", {})
    if not isinstance(requirements, dict):
        raise ValueError("Requirements must be an object")
    pages = requirements.get("pages", 8)
    if type(pages) is not int or not 3 <= pages <= 20:
        raise ValueError("Slide count must be between 3 and 20")
    if not requirements.get("external_model_acknowledged"):
        raise ValueError("External model acknowledgement is required")
    return task


def configure(home: Path, result: Path, model: str, base_url: str) -> None:
    import yaml
    from urllib.parse import urlsplit
    url = urlsplit(base_url)
    proxy_origin = os.environ.get("PPT_MODEL_PROXY_ORIGIN", "")
    is_scoped_proxy = bool(proxy_origin) and base_url == proxy_origin.rstrip("/") + "/v1" and url.scheme == "http" and url.hostname == "model-proxy" and url.port == 8080
    if (url.scheme != "https" and not is_scoped_proxy) or url.username or url.password or url.query or url.fragment or not url.hostname:
        raise ValueError("Model URL must be HTTPS or the exact operator-configured model proxy")
    # This is an operator supplied model origin. The execution namespace must
    # independently enforce its approved egress proxy; URL checks are no firewall.
    config = {
        # Conservative service budget matching Hermes' existing fallback. This
        # avoids external model-metadata discovery inside the isolated worker;
        # it is not a claim about the provider's maximum context window.
        "model": {"provider": "ppt-model", "default": model, "supports_vision": True,
                  "context_length": 256000},
        "database": {"journal_mode": "delete"},
        "providers": {"ppt-model": {"base_url": base_url, "api_mode": "codex_responses", "key_env": "PPT_MODEL_API_KEY", "default_model": model, "discover_models": False, "models": [model]}},
        "fallback_providers": [],
        "terminal": {"backend": "local", "cwd": "/work/project", "timeout": 120},
        "gateway": {"multiplex_profiles": False, "api_server": {"enabled": True, "host": "127.0.0.1", "port": 8642, "max_concurrent_runs": 1}},
        "platform_toolsets": {"api_server": ["terminal", "file", "skills", "todo", "vision"]},
        "agent": {"max_turns": 100, "image_input_mode": "native", "disabled_toolsets": ["delegation", "cronjob", "memory", "session_search", "web", "browser", "image_gen", "connections", "code_execution", "clarify"]},
        "kanban": {"dispatch_in_gateway": False},
        "plugins": {"enabled": ["ppt-service-policy"], "hook_callback_timeout": 5},
    }
    home.mkdir(parents=True, exist_ok=True)
    # Each attempt restores a controlled checkpoint. Service settings and policy
    # bootstrap are overwritten from immutable code, not inherited user plugins.
    (home / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (home / "config.yaml").chmod(0o600)
    plugin = home / "plugins" / "ppt-service-policy"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.yaml").write_text('name: ppt-service-policy\nversion: "1.0"\ndescription: PPT execution policy and usage observer\n', encoding="utf-8")
    (plugin / "__init__.py").write_text("from ppt_service.worker.policy_plugin import register\n", encoding="utf-8")
    skills = home / "skills" / "ppt-master"
    skills.parent.mkdir(parents=True, exist_ok=True)
    expected = Path(os.environ.get("PPT_MASTER_SKILL_DIR", "/opt/ppt-master/skills/ppt-master"))
    if skills.is_symlink():
        if skills.resolve() != expected.resolve():
            raise ValueError("Unexpected skill link")
    elif skills.exists():
        raise ValueError("Skill must come from the immutable image")
    else:
        skills.symlink_to(expected, target_is_directory=True)
    result.mkdir(parents=True, exist_ok=True)
    marker = result / "policy-ready.json"
    if marker.exists() or marker.is_symlink():
        marker.unlink()


def stop_process_tree(process: subprocess.Popen, grace: float = 20) -> None:
    if process.poll() is not None:
        # The process group can still contain descendants after the leader exits.
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            process.wait(timeout=1)
            return
        if process.poll() is not None:
            # Reap the leader but still check its entire process group.
            process.wait()
        time.sleep(0.2)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Gateway process did not stop") from None


def run(task: dict | None, *, probe_only: bool, deadline_seconds: int) -> int:
    home, result = Path("/work/home"), Path("/work/result")
    model = os.environ.get("PPT_MODEL", "gpt-6.1-sol")
    base_url = os.environ.get("PPT_MODEL_BASE_URL", "https://custom-llm-proxy.panghuer.top/v1")
    if not probe_only and not os.environ.get("PPT_MODEL_API_KEY"):
        raise ValueError("Dedicated model credential is not configured")
    configure(home, result, model, base_url)
    key = secrets.token_urlsafe(48)
    env = dict(os.environ)
    env.update({"HOME": str(home), "HERMES_HOME": str(home), "TERMINAL_CWD": "/work/project", "API_SERVER_ENABLED": "true", "API_SERVER_KEY": key, "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": "8642", "PPT_RESULT_DIR": str(result), "HERMES_NON_INTERACTIVE": "1"})
    # Do not inherit arbitrary authentication/platform flags from a parent image.
    for name in tuple(env):
        if name.startswith(("TELEGRAM_", "DISCORD_", "SLACK_", "HERMES_DASHBOARD_")):
            env.pop(name)
    log_path = result / "gateway.private.log"
    started = time.monotonic()
    process = None
    preview = None
    client = HermesClient("http://127.0.0.1:8642", key, timeout=10)
    state = {"schema_version": 1, "status": "starting", "probe_only": probe_only}
    interrupted = False

    def on_signal(signum, frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        with log_path.open("wb") as log:
            log_path.chmod(0o600)
            process = subprocess.Popen(["/opt/hermes/.venv/bin/hermes", "gateway", "run", "--no-supervise", "--external-supervisor"], cwd="/work/project", env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            capabilities = None
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("Dedicated gateway exited before readiness")
                if interrupted:
                    raise RuntimeError("Execution interrupted during startup")
                try:
                    capabilities = client.capabilities()
                    break
                except HermesError:
                    time.sleep(0.5)
            if capabilities is None:
                raise RuntimeError("Dedicated gateway readiness timed out")
            marker_path = result / "policy-ready.json"
            if not marker_path.is_file() or marker_path.is_symlink():
                raise RuntimeError("Execution policy did not register")
            marker = json.loads(marker_path.read_text())
            if marker.get("pid") != process.pid or marker.get("policy_version") != 1:
                raise RuntimeError("Execution policy identity mismatch")
            session_id = f"probe-{uuid.uuid4().hex}" if probe_only else task["session_id"]
            try:
                created = client.create_session({"id": session_id, "title": "PPT service isolated phase", "source": "api_server"})
                if created.get("session", {}).get("id") != session_id:
                    raise RuntimeError("Session creation contract mismatch")
            except HermesError as exc:
                if exc.status != 409 or probe_only:
                    raise
                existing = client.json("GET", f"/api/sessions/{safe_id(session_id)}")
                if existing.get("session", {}).get("id") != session_id:
                    raise RuntimeError("Restored session contract mismatch")
            if probe_only:
                state.update({"status": "probe_passed", "session_created": True, "capabilities": capabilities, "gateway_pid": process.pid, "model_requests": 0})
                return 0
            instructions = stage_instructions(task["phase"])
            if task["phase"] in {"generate", "revise"}:
                preview = OfficialPreview(Path("/work/project"), lifetime=deadline_seconds + 120)
                preview.start(lambda: interrupted)
                instructions += preview.instructions()
                state["preview_ready"] = True
            if interrupted:
                raise RuntimeError("Execution interrupted before model admission")
            admission = client.admit(session_id, input_message(task), f"ppt-{task['job_id']}-{task['attempt']}-{task['phase']}", instructions=instructions)
            run_id = admission.get("run_id", "")
            safe_id(run_id)
            state.update({"job_id": task["job_id"], "attempt": task["attempt"], "phase": task["phase"], "session_id": session_id, "run_id": run_id, "status": "running"})
            atomic_json(result / "execution.json", state)
            stop_sent = False
            status_retries = 0
            while time.monotonic() - started < deadline_seconds:
                remaining = deadline_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                # A poll never receives a fresh phase deadline. In particular,
                # the first Agent initialization can outlive one HTTP request.
                client.timeout = min(10, remaining)
                if interrupted and not stop_sent:
                    client.stop(run_id)
                    stop_sent = True
                remaining = deadline_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                client.timeout = min(10, remaining)
                try:
                    status = client.status(run_id)
                except HermesError as exc:
                    # Retry only the existing run's status GET. Bad JSON,
                    # authentication, missing runs and admission are not retried.
                    transient = (exc.operation == f'/v1/runs/{safe_id(run_id)}'
                                 and (exc.status is None or exc.status in (502, 503, 504)))
                    if not transient or process.poll() is not None or status_retries >= 3:
                        raise
                    remaining = deadline_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    if interrupted and not stop_sent:
                        continue  # Send cancellation before another poll/backoff.
                    status_retries += 1
                    state['hermes_status_retries'] = status_retries
                    time.sleep(min(2, remaining))
                    continue
                observed = status.get("status")
                if observed in TERMINAL:
                    # Private transcript is retained for operator-only acceptance;
                    # no token text or raw tool events are exposed as business SSE.
                    state.update({"status": observed, "hermes_status": observed, "agent_turn_ended": True, "usage": status.get("usage"), "runtime": status.get("runtime"), "elapsed_seconds": round(time.monotonic() - started, 2)})
                    atomic_json(result / "agent-response.private.json", {"output": status.get("output", "")})
                    if observed != "completed" or interrupted:
                        if interrupted:
                            state["status"] = "interrupted"
                        return 2
                    # Hermes finishing a turn is not proof of a phase handoff.
                    # Never infer a manifest from its prose or private transcript.
                    state["candidate_validated"] = False
                    try:
                        manifest_status = candidate_handoff(result / "manifest.json", task)
                    except FileNotFoundError:
                        state.update({"status": "candidate_output_missing", "candidate_output_status": "missing"})
                        return 4
                    except (ValueError, OSError, RecursionError):
                        state.update({"status": "candidate_output_rejected", "candidate_output_status": "rejected"})
                        return 4
                    state.update({"candidate_output_status": "present", "manifest_status": manifest_status})
                    return 0
                if process.poll() is not None:
                    raise RuntimeError("Gateway exited during task execution")
                time.sleep(min(2, max(0, deadline_seconds - (time.monotonic() - started))))
            try:
                client.stop(run_id)
            except HermesError:
                pass
            state["status"] = "deadline_exceeded"
            return 3
    except (ValueError, RuntimeError, HermesError, OSError) as exc:
        state.update({"status": "failed", "error_type": type(exc).__name__,
                      "elapsed_seconds": round(time.monotonic() - started, 2)})
        if isinstance(exc, HermesError):
            state.update(_hermes_failure(exc))
        # Error messages are intentionally not printed: upstream exceptions may
        # carry request details. Operator-only gateway log remains in this attempt.
        return 1
    finally:
        cleanup_failed = False
        try:
            if process is not None:
                stop_process_tree(process)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            cleanup_failed = True
        finally:
            try:
                if preview is not None:
                    preview.stop()
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                cleanup_failed = True
        state["gateway_stopped"] = process is None or process.poll() is not None
        state["preview_stopped"] = preview is None or preview.stopped
        if cleanup_failed:
            state.update({"status": "failed", "error_type": "CleanupError"})
        state["delivery_published"] = False
        atomic_json(result / "execution.json", state)
        print(json.dumps({key: state[key] for key in ("status", "probe_only", "gateway_stopped", "delivery_published")}, ensure_ascii=False))
        if cleanup_failed:
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Dedicated PPT execution adapter (candidate output only)")
    parser.add_argument("--probe-only", action="store_true", help="Start isolated gateway and create a session, without a model call")
    parser.add_argument("--task", type=Path)
    parser.add_argument("--deadline", type=int, default=1800)
    args = parser.parse_args()
    if not args.probe_only and args.task is None:
        parser.error("--task is required outside probe mode")
    if not 30 <= args.deadline <= 3600:
        parser.error("deadline must be 30–3600 seconds")
    task = None if args.probe_only else read_task(args.task)
    return run(task, probe_only=args.probe_only, deadline_seconds=args.deadline)


if __name__ == "__main__":
    raise SystemExit(main())
