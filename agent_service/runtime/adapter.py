"""Pinned Pi JSONL print-mode adapter. No shell, environment inheritance or fallback.

Only fixture_mode permits same-UID execution. Production requires a Linux root
controller, unique child UID, capability clearing and explicit acceptance evidence.
A delegated cgroup v2 subtree is optional stronger process containment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping
import uuid

from .contracts import ContractError, parse_timestamp, utc_now
from .profiles import ExecutionProfile, ProfileError, bounded_provenance
from ..diagnostics import log_execution_failure

PI_PACKAGE = "@mariozechner/pi-coding-agent"
PI_VERSION = "0.73.1"
COMPACTOR_PATH = Path(__file__).with_name("jsonl_compactor.mjs")
COMPACTOR_SHA256 = "4ffaeac575e82d149b7104a6e48ff928dbf5a9cd77232ba22cd6415fbb0a8d89"
OUTPUT_STAT_KEYS = frozenset({"raw_bytes", "raw_lines", "raw_max_line_bytes", "output_bytes",
                              "output_lines", "output_max_line_bytes", "message_updates", "tool_updates"})
MODEL_RETRY_DELAY_SECONDS = 2.0


class RuntimeExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message[:2000], retryable


@dataclass(frozen=True)
class RuntimeSettings:
    command: tuple[str, ...] = ("pi",)
    node_command: tuple[str, ...] = ("node",)
    provider: str = ""
    model: str = ""
    model_base_url: str | None = None
    model_api: str | None = None
    model_compat: Mapping[str, bool | str] = field(default_factory=dict)
    model_max_tokens: int | None = None
    model_input: tuple[str, ...] | None = None
    expected_pi_version: str = PI_VERSION
    skill_paths: tuple[Path, ...] = ()
    skill_hashes: tuple[str, ...] = ()
    bundle_manifest: Path | None = None
    bundle_manifest_sha256: str | None = None
    bundle_root: Path | None = None
    skill_bundle_revision: str = ""
    runtime_revision: str = ""
    image_digest: str | None = None
    model_env: Mapping[str, str] = field(default_factory=dict, repr=False)
    tool_pythonpath: Path | None = None
    tool_proxy_base_url: str | None = None
    fixture_mode: bool = False
    compact_events: bool = False
    capability_verified: bool = False
    model_verified: bool = False
    isolation_verified: bool = False
    control_dir: Path | None = None
    cgroup_root: Path | None = None
    run_uid_start: int = 20000
    run_gid: int = 20000
    denied_paths: tuple[Path, ...] = ()
    term_grace_seconds: float = 2.0
    kill_grace_seconds: float = 3.0
    idle_timeout_seconds: float = 120.0
    max_output_bytes: int = 32 * 1024 * 1024
    max_line_bytes: int = 4 * 1024 * 1024
    max_processes: int = 64


class PiRuntime:
    def __init__(self, settings: RuntimeSettings | None = None, *, profile: ExecutionProfile):
        self.settings = settings or RuntimeSettings()
        self.profile = profile
        if not isinstance(self.profile, ExecutionProfile):
            raise TypeError("Execution profile must be an instance constructed by trusted server code")
        self._lock = threading.Lock()
        self._unsafe = False
        self._probe_lock = threading.Lock()
        self._version_cache = None

    @property
    def ready(self) -> bool:
        return self.health()["ready"]

    @property
    def capabilities(self):
        # A neutral runtime grants no domain workflow or continuation protocol.
        return {}

    def health(self) -> dict:
        try:
            self._check_settings()
            self._verify_resources()
            pi_version, node_version = self._readiness_versions()
        except (RuntimeExecutionError, OSError, ValueError, KeyError, TypeError) as exc:
            return {"ready": False, "code": getattr(exc, "code", "runtime_not_ready"),
                    "message": getattr(exc, "message", "Runtime filesystem prerequisites are unavailable"),
                    "fixture_mode": self.settings.fixture_mode}
        return {"ready": True, "fixture_mode": self.settings.fixture_mode,
                "package": PI_PACKAGE, "expected_pi_version": self.settings.expected_pi_version,
                "pi_version": pi_version, "node_version": node_version}

    def _check_settings(self):
        s = self.settings
        if self._unsafe:
            raise RuntimeExecutionError("process_safety_unverified", "Runtime is fenced until successful orphan recovery")
        if not s.provider or not s.model or not s.command or not s.node_command:
            raise RuntimeExecutionError("configuration_missing", "Explicit Pi command, provider and model are required")
        if type(s.compact_events) is not bool:
            raise RuntimeExecutionError("configuration_missing", "Event compaction must be an explicit trusted setting")
        if s.compact_events and (len(s.command) != 2 or len(s.node_command) != 1
                                or s.command[0] != s.node_command[0]
                                or Path(s.command[1]).name != "cli.js" or s.expected_pi_version != PI_VERSION):
            raise RuntimeExecutionError("configuration_missing", "Event compaction requires the pinned Node Pi CLI entry")
        if s.model_base_url is not None or s.model_api is not None:
            from urllib.parse import urlsplit
            parsed = urlsplit(s.model_base_url or "")
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise RuntimeExecutionError("configuration_missing", "Custom model endpoint must be an explicit HTTP(S) URL without embedded credentials")
            if s.model_api not in ("openai-completions", "openai-responses"):
                raise RuntimeExecutionError("configuration_missing", "Custom model API must be explicitly verified OpenAI completions or responses")
            if not s.model_env.get("AGENT_MODEL_API_KEY"):
                raise RuntimeExecutionError("configuration_missing", "Custom model endpoint requires its dedicated worker API key")
        if not isinstance(s.model_compat, Mapping):
            raise RuntimeExecutionError("configuration_missing", "Model compatibility must be a trusted mapping")
        for key, value in s.model_compat.items():
            if key in {"supportsStore", "supportsUsageInStreaming", "supportsReasoningEffort"}:
                valid = type(value) is bool
            elif key == "maxTokensField":
                valid = type(value) is str and value in {"max_tokens", "max_completion_tokens"}
            else:
                valid = False
            if not valid:
                raise RuntimeExecutionError("configuration_missing", "Unsupported model compatibility option or value")
        if s.model_compat and s.model_api != "openai-completions":
            raise RuntimeExecutionError("configuration_missing", "Completions compatibility requires openai-completions")
        if s.model_max_tokens is not None and (type(s.model_max_tokens) is not int or not 1 <= s.model_max_tokens <= 8192):
            raise RuntimeExecutionError("configuration_missing", "Model token limit must be an integer from 1 to 8192")
        if s.model_input is not None and (type(s.model_input) is not tuple
                or any(type(item) is not str for item in s.model_input)
                or s.model_input not in (("text",), ("text", "image"))):
            raise RuntimeExecutionError("configuration_missing", "Model inputs must explicitly select text or text and image")
        if (s.model_compat or s.model_max_tokens is not None or s.model_input is not None) and not s.model_base_url:
            raise RuntimeExecutionError("configuration_missing", "Custom model settings require an explicit endpoint")
        if not (0 <= s.term_grace_seconds <= 10 and 0 < s.kill_grace_seconds <= 10):
            raise RuntimeExecutionError("configuration_missing", "Invalid bounded shutdown configuration")
        if type(s.max_processes) is not int or not 2 <= s.max_processes <= 4096:
            raise RuntimeExecutionError("configuration_missing", "Process limit must be an integer from 2 to 4096")
        if not s.skill_bundle_revision or not s.runtime_revision:
            raise RuntimeExecutionError("configuration_missing", "Pinned runtime and skill revisions are required")
        if len(s.skill_paths) != len(s.skill_hashes) or not s.skill_paths:
            raise RuntimeExecutionError("configuration_missing", "Selected skills and reviewed SHA256 hashes are required")
        # Never allow model_env to inject loader, shell or Python execution hooks.
        for key, value in s.model_env.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:API_KEY|AUTH_TOKEN)", key) or not isinstance(value, str):
                raise RuntimeExecutionError("configuration_missing", "Only explicit provider credential environment entries are accepted")
            if any(word in key for word in ("WORKER", "VAULT", "KUBE", "SQLITE", "CONTROL", "REDIS", "SMTP")):
                raise RuntimeExecutionError("configuration_missing", "Control credentials cannot enter the model environment")
        if s.tool_proxy_base_url is not None and s.tool_proxy_base_url != s.model_base_url:
            raise RuntimeExecutionError('configuration_missing', 'Tools must use the fixed task proxy origin')
        if s.fixture_mode:
            return
        if not self.profile.production_ready:
            raise RuntimeExecutionError("runtime_not_ready", "Execution profile has no verified production runtime boundary")
        if not (s.capability_verified and s.model_verified and s.isolation_verified):
            raise RuntimeExecutionError("runtime_not_ready", "Production capability, model/tool roundtrip and isolation evidence are required")
        if not s.image_digest or not re.fullmatch(r"sha256:[0-9a-f]{64}", s.image_digest):
            raise RuntimeExecutionError("runtime_not_ready", "Verified image digest is required")
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeExecutionError("runtime_not_ready", "Production requires a Linux root controller with isolated child UID")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise RuntimeExecutionError("runtime_not_ready", "Linux pidfd support is required for process cleanup")
        if not s.control_dir or not s.denied_paths:
            raise RuntimeExecutionError("runtime_not_ready", "Private control directory and isolation sentinels are required")
        st = s.control_dir.stat()
        if st.st_uid != 0 or st.st_mode & 0o077 or s.control_dir.is_symlink():
            raise RuntimeExecutionError("runtime_not_ready", "Control directory must be root-owned mode 0700")
        if s.cgroup_root and not (s.cgroup_root / "cgroup.controllers").is_file():
            raise RuntimeExecutionError("runtime_not_ready", "Configured optional cgroup subtree is not cgroup v2")
        if s.run_uid_start < 10000 or s.run_gid < 10000:
            raise RuntimeExecutionError("configuration_missing", "Dedicated child UID/GID range is required")

    def _custom_model_definition(self):
        """Only trusted settings reach models.json; no fields come from jobs."""
        model = {"id": self.settings.model}
        if self.settings.model_max_tokens is not None:
            model["maxTokens"] = self.settings.model_max_tokens
        if self.settings.model_compat:
            model["compat"] = dict(self.settings.model_compat)
        if self.settings.model_input is not None:
            model["input"] = list(self.settings.model_input)
        return model

    @staticmethod
    def _model_request_error(message):
        # Pinned Pi 0.73.1 preserves OpenAI APIError.message as "<status> <body>".
        # Verified against actual loopback 429/503 responses. Never search an
        # arbitrary body for numbers or retry generic provider/protocol errors.
        raw = message.get("errorMessage")
        status = (re.match(r"\A(429|500|502|503|504) (?=\S)", raw)
                  if message.get("stopReason") == "error" and isinstance(raw, str) else None)
        if status:
            return RuntimeExecutionError("model_http_transient",
                                         "Pi provider returned retryable HTTP " + status.group(1), True)
        return RuntimeExecutionError("model_error", "Pi assistant reported a failed model request")

    @staticmethod
    def _wait_for_model_retry(cancel, end_time):
        # Called only after the entire child tree has been fenced. The scheduler
        # owns the next fresh attempt and its existing two-attempt cap; there is
        # no model request or retry loop here and no deadline reset.
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            raise RuntimeExecutionError("timed_out", "Execution deadline elapsed before model retry")
        if cancel.wait(min(MODEL_RETRY_DELAY_SECONDS, remaining)):
            raise RuntimeExecutionError("cancelled", "Execution cancelled during model retry backoff")
        if time.monotonic() >= end_time:
            raise RuntimeExecutionError("timed_out", "Execution deadline elapsed during model retry backoff")

    def _verify_resources(self):
        s = self.settings
        if s.compact_events and (COMPACTOR_PATH.is_symlink() or not COMPACTOR_PATH.is_file()
                               or hashlib.sha256(COMPACTOR_PATH.read_bytes()).hexdigest() != COMPACTOR_SHA256):
            raise RuntimeExecutionError("runtime_not_ready", "Reviewed event compactor hash does not match")
        for path, expected in zip(s.skill_paths, s.skill_hashes):
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeExecutionError("runtime_not_ready", "Selected reviewed skill hash does not match")
        if s.bundle_manifest:
            manifest_path = s.bundle_manifest.resolve()
            raw_manifest = manifest_path.read_bytes()
            if not s.bundle_manifest_sha256 or hashlib.sha256(raw_manifest).hexdigest() != s.bundle_manifest_sha256:
                raise RuntimeExecutionError("runtime_not_ready", "Reviewed skill manifest hash does not match")
            manifest = json.loads(raw_manifest)
            if manifest["bundle_revision"] != s.skill_bundle_revision:
                raise RuntimeExecutionError("runtime_not_ready", "Reviewed skill bundle revision mismatch")
            resource_root = (s.bundle_root or manifest_path.parent).resolve()
            for item in manifest["local_files"]:
                relative = Path(item["path"])
                resource = resource_root / relative
                if relative.is_absolute() or ".." in relative.parts or resource.is_symlink() or not resource.resolve().is_relative_to(resource_root):
                    raise RuntimeExecutionError("runtime_not_ready", "Unsafe reviewed skill resource path")
                if hashlib.sha256(resource.read_bytes()).hexdigest() != item["sha256"]:
                    raise RuntimeExecutionError("runtime_not_ready", "Reviewed skill resource hash does not match")
        elif not s.fixture_mode:
            raise RuntimeExecutionError("runtime_not_ready", "Production requires a pinned reviewed skill resource manifest")

    def _readiness_versions(self):
        # Probe trusted installed executables, never a model endpoint. File
        # identity invalidates cache immediately; TTL bounds other drift.
        s = self.settings
        identity = []
        for command in (s.command, s.node_command):
            executable = shutil.which(command[0]) or command[0]
            if not Path(executable).is_file():
                raise RuntimeExecutionError("runtime_not_ready", "Runtime executable is unavailable")
            for arg in (executable, *command[1:]):
                path = Path(arg)
                if path.is_file():
                    st = path.stat()
                    identity.append((str(path.resolve()), st.st_ino, st.st_size, st.st_mtime_ns))
            identity.append(tuple(command))
        key = tuple(identity)
        with self._probe_lock:
            if self._version_cache and self._version_cache[0] == key and time.monotonic() - self._version_cache[1] < 30:
                return self._version_cache[2:]
            import tempfile
            with tempfile.TemporaryDirectory(prefix="pi-readiness-", dir=s.control_dir) as directory:
                home = Path(directory)
                env = self._environment(home, home / "agent", home)
                pi_version = self._probe(s.command, env, home)
                node_version = self._probe(s.node_command, env, home)
            if pi_version != s.expected_pi_version:
                raise RuntimeExecutionError("runtime_not_ready", "Pi version does not match the reviewed pin")
            if int(node_version.split(".")[0]) < 20:
                raise RuntimeExecutionError("runtime_not_ready", "Node version is below the supported minimum")
            self._version_cache = (key, time.monotonic(), pi_version, node_version)
            return pi_version, node_version

    def recover(self, uids=()) -> bool:
        """Stop all persisted attempt UIDs before the scheduler recovers its queue.

        Caller must hold the worker instance lock. Failure blocks queue recovery.
        Unknown fixture journals are deliberately not treated as safe.
        """
        if not self._lock.acquire(blocking=False):
            raise RuntimeExecutionError("process_safety_unverified", "Runtime is still executing")
        try:
            control = self.settings.control_dir
            if control is None:
                if self.settings.fixture_mode and not tuple(uids):
                    return True
                raise RuntimeExecutionError("runtime_not_ready", "Recovery requires a private control directory")
            if not self.settings.fixture_mode:
                if sys.platform != "linux" or os.geteuid() != 0:
                    raise RuntimeExecutionError("process_safety_unverified", "UID recovery requires Linux root controller")
                for uid in uids:
                    self._stop_uid(uid)
            for journal in control.glob("runtime-*.json"):
                try:
                    record = json.loads(journal.read_text(encoding="utf-8"))
                    if record.get("kind") not in ("uid", "cgroup"):
                        raise ValueError("No durable containment")
                    self._stop_uid(record["uid"])
                    if record.get("cgroup"):
                        root = self.settings.cgroup_root
                        group = Path(record["cgroup"])
                        if root is None or group.parent.resolve() != root.resolve() or not re.fullmatch(r"agent-pi-[0-9a-f]{32}", group.name):
                            raise ValueError("Invalid cgroup")
                        if group.exists():
                            self._kill_cgroup(group)
                            group.rmdir()
                    journal.unlink()
                except (OSError, ValueError, KeyError, RuntimeExecutionError) as exc:
                    self._unsafe = True
                    raise RuntimeExecutionError("process_safety_unverified", "Old runtime process containment cannot be verified; queue recovery blocked") from exc
            self._unsafe = False
            return True
        except BaseException:
            self._unsafe = True
            raise
        finally:
            self._lock.release()

    @staticmethod
    def _uid_processes(uid):
        if type(uid) is not int or not 10000 <= uid <= 2**31 - 1:
            raise RuntimeExecutionError("process_safety_unverified", "Invalid dedicated attempt UID")
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeExecutionError("process_safety_unverified", "UID verification requires Linux root controller")
        matches = []
        for path in Path("/proc").iterdir():
            if not path.name.isdecimal():
                continue
            try:
                fields = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines() if ":" in line)
            except FileNotFoundError:
                continue  # Process reaped during the scan.
            except OSError as exc:
                raise RuntimeExecutionError("process_safety_unverified", "Cannot inspect all process credentials") from exc
            try:
                ids = [int(value) for value in fields["Uid"].split()]
                state = fields["State"].strip().split()[0]
            except (KeyError, ValueError, IndexError) as exc:
                raise RuntimeExecutionError("process_safety_unverified", "Cannot parse process credential state") from exc
            if uid in ids and state not in ("Z", "X"):
                matches.append(int(path.name))
        return matches

    def _stop_uid(self, uid):
        deadline = time.monotonic() + self.settings.kill_grace_seconds
        empty_scans = 0
        while time.monotonic() < deadline:
            pids = self._uid_processes(uid)
            if not pids:
                empty_scans += 1
                if empty_scans >= 2:
                    return
            else:
                empty_scans = 0
                for pid in pids:
                    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                        raise RuntimeExecutionError("process_safety_unverified", "Kernel/Python pidfd support is required for safe UID cleanup")
                    try:
                        descriptor = os.pidfd_open(pid)
                    except ProcessLookupError:
                        continue
                    try:
                        if pid in self._uid_processes(uid):
                            try:
                                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    finally:
                        os.close(descriptor)
            time.sleep(0.02)
        raise RuntimeExecutionError("process_safety_unverified", "Dedicated attempt UID still has live processes")

    def _environment(self, home: Path, agent: Path, tmp: Path) -> dict[str, str]:
        # PATH is necessary for reviewed tools. Production supplies an immutable
        # image PATH; no arbitrary controller environment entries survive.
        env = {"PATH": "/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin" if os.name != "nt" else os.environ.get("PATH", ""),
               "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
               "XDG_CACHE_HOME": str(home / ".cache"), "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp),
               "PI_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1", "NO_COLOR": "1", "LANG": "C.UTF-8"}
        if os.name == "nt":
            for key in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
                if key in os.environ:
                    env[key] = os.environ[key]
        if self.settings.tool_pythonpath:
            env["PYTHONPATH"] = str(self.settings.tool_pythonpath.resolve())
        if self.settings.tool_proxy_base_url:
            env['AGENT_TOOL_PROXY_BASE_URL'] = self.settings.tool_proxy_base_url
        if getattr(self.profile, 'shared_project', False):
            env['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/ms-playwright'
            env['PPT_MASTER_SKILL_DIR'] = '/opt/ppt-master/skills/ppt-master'
        env.update(self.settings.model_env)
        return env

    @staticmethod
    def _probe(command, env, cwd):
        process = None
        chunks = queue.Queue(maxsize=2)
        def read_version(pipe, stream):
            try:
                chunks.put((stream, pipe.read(101)))
            finally:
                pipe.close()
        try:
            process = subprocess.Popen([*command, "--version"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                                       close_fds=True)
            for stream, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                threading.Thread(target=read_version, args=(pipe, stream), daemon=True).start()
            responses = []
            deadline = time.monotonic() + 5
            while len(responses) < 2:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 5)
                stream, data = chunks.get(timeout=remaining)
                if len(data) > 100:
                    raise RuntimeExecutionError("runtime_not_ready", "Runtime version output exceeds its bound")
                text = data.decode("utf-8", errors="strict").strip()
                if text and not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", text):
                    raise RuntimeExecutionError("runtime_not_ready", "Runtime version probe returned an unexpected response")
                responses.append(text.lstrip("v"))
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            versions = {value for value in responses if value}
            if code or len(versions) != 1:
                raise RuntimeExecutionError("runtime_not_ready", "Runtime version probe returned an unexpected response")
            return versions.pop()
        except (OSError, UnicodeError, subprocess.TimeoutExpired, queue.Empty) as exc:
            raise RuntimeExecutionError("runtime_not_ready", "Runtime version probe failed") from exc
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=1)

    def _prepare_cgroup(self, workspace: Path, command: list[str], uid: int):
        s = self.settings
        control = s.control_dir.resolve()
        self._stop_uid(uid)  # Refuse a UID with surviving processes before reuse.
        group = None
        if s.cgroup_root:
            group = s.cgroup_root.resolve() / ("agent-pi-" + uuid.uuid4().hex)
            group.mkdir()
            if not (group / "cgroup.kill").exists():
                group.rmdir()
                raise RuntimeExecutionError("runtime_not_ready", "Delegated cgroup.kill is unavailable")
            (group / "pids.max").write_text(str(s.max_processes))
        protected = set(getattr(self.profile, 'protected_paths', ()))
        shared_project = getattr(self.profile, 'shared_project', False)
        for root, dirs, files in os.walk(workspace, topdown=True, followlinks=False):
            if Path(root) == workspace:
                dirs[:] = [name for name in dirs if name not in protected]
            here = Path(root)
            project_directory = here == workspace / 'project' or (workspace / 'project') in here.parents
            mode = 0o710 if shared_project and here == workspace else (
                0o2770 if shared_project and project_directory else 0o700)
            # CAP_CHOWN and DAC_OVERRIDE do not grant chmod on an Agent-owned
            # inode. Set its final mode while the controller owns it, then
            # transfer ownership; no CAP_FOWNER is needed by the capsule.
            os.chown(root, 0, 0)
            os.chmod(root, mode)
            os.chown(root, uid, uid)
            for name in files:
                path = here / name
                if shared_project and project_directory:
                    os.chown(path, 0, 0)
                    os.chmod(path, 0o660)
                os.chown(path, uid, uid)
        config = control / ("launch-" + uuid.uuid4().hex + ".json")
        self._atomic(config, json.dumps({"controller_pid": os.getpid(), "max_processes": s.max_processes,
                     "cgroup": str(group) if group else None, "uid": uid, "gid": uid,
                     "denied_paths": [str(control), *[str(p.resolve()) for p in s.denied_paths]],
                     "cwd": str(workspace), "command": command,
                     "umask": 0o007 if getattr(self.profile, 'shared_project', False) else 0o077}))
        launched = [sys.executable, str(Path(__file__).with_name("launcher.py")), str(config)]
        return group, config, launched

    @staticmethod
    def _atomic(path: Path, content: str):
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _cgroup_alive(group):
        return "populated 1" in (group / "cgroup.events").read_text()

    def _kill_cgroup(self, group):
        (group / "cgroup.kill").write_text("1")
        deadline = time.monotonic() + self.settings.kill_grace_seconds
        while self._cgroup_alive(group) and time.monotonic() < deadline:
            time.sleep(0.02)
        if self._cgroup_alive(group):
            raise RuntimeExecutionError("process_safety_unverified", "Cgroup process tree did not stop")

    def execute(self, job: dict, workspace: Path, event_sink: Callable[[dict], None],
                cancel_event: threading.Event) -> None:
        if not self._lock.acquire(blocking=False):
            raise RuntimeExecutionError("runtime_busy", "Only one runtime execution is allowed")
        self._diagnostic_stage = 'check_settings'
        try:
            self._execute(job, Path(workspace), event_sink, cancel_event)
        except Exception as exc:
            log_execution_failure('pi_runtime', self._diagnostic_stage, exc)
            if isinstance(exc, RuntimeExecutionError):
                raise
            if isinstance(exc, ProfileError):
                raise RuntimeExecutionError(exc.code, str(exc)) from exc
            if isinstance(exc, (OSError, ValueError, ContractError)):
                raise RuntimeExecutionError("runtime_error", "Runtime configuration, filesystem or job validation failed") from exc
            raise
        finally:
            self._lock.release()

    def _execute(self, job, workspace, sink, cancel):
        s = self.settings
        self._check_settings()
        # B may include attempt in the internal dispatch object, but the shared
        # wire schema remains strict and is validated without this local field.
        wire = {k: v for k, v in job.items() if k not in {"attempt", "_runtime_uid"}}
        profile = self.profile
        self._diagnostic_stage = 'validate_request'
        uid = job.get("_runtime_uid")
        if not s.fixture_mode and (type(uid) is not int or not 10000 <= uid <= 2**31 - 1):
            raise RuntimeExecutionError("runtime_not_ready", "Worker must persist a unique never-reused attempt UID")
        checked = profile.validate(wire)
        if (checked.runtime_revision != s.runtime_revision or checked.skill_bundle_revision != s.skill_bundle_revision):
            raise RuntimeExecutionError("runtime_not_ready", "Accepted job revisions do not match runtime configuration")
        if cancel.is_set():
            raise RuntimeExecutionError("cancelled", "Execution cancelled before startup")
        seconds = min(checked.timeout_seconds, (parse_timestamp(checked.execution_deadline_at) - datetime.now(timezone.utc)).total_seconds())
        if seconds <= 0:
            raise RuntimeExecutionError("timed_out", "Execution deadline elapsed")
        end_time = time.monotonic() + seconds
        self._diagnostic_stage = 'prepare_workspace'
        if workspace.is_symlink():
            raise RuntimeExecutionError("unsafe_workspace", "Attempt workspace must not be a symlink")
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(workspace.iterdir()):
            raise RuntimeExecutionError("unsafe_workspace", "A fresh empty attempt workspace is required")
        workspace = workspace.resolve()
        home, agent, tmp = workspace / ".home", workspace / ".home" / ".pi" / "agent", workspace / ".tmp"
        agent.mkdir(parents=True, mode=0o700)
        tmp.mkdir(mode=0o700)
        prepare = getattr(profile, 'prepare_workspace', None)
        if prepare is not None:
            self._diagnostic_stage = 'prepare_profile_workspace'
            prepare(wire, workspace)
        self._diagnostic_stage = 'verify_resources'
        self._verify_resources()
        env = self._environment(home, agent, tmp)
        # One retry owner: disable both Pi's session retry and the underlying
        # provider SDK retry in this fresh task-only settings file. Otherwise a
        # single scheduler attempt can silently make several HTTP requests.
        self._diagnostic_stage = 'write_agent_config'
        self._atomic(agent / "settings.json", json.dumps({"retry": {
            "enabled": False, "provider": {"maxRetries": 0}}}))
        if s.model_base_url:
            self._atomic(agent / "models.json", json.dumps({"providers": {s.provider: {
                "baseUrl": s.model_base_url, "api": s.model_api,
                "apiKey": "AGENT_MODEL_API_KEY", "authHeader": True,
                "models": [self._custom_model_definition()]}}}))
        # Resolve commands now, before replacing cwd/PATH. Explicit absolute
        # executable paths are strongly preferred in the immutable image.
        command = list(s.command)
        command[0] = shutil.which(command[0]) or command[0]
        self._diagnostic_stage = 'probe_versions'
        node = self._probe(s.node_command, env, workspace)
        version = self._probe(command, env, workspace)
        if time.monotonic() >= end_time:
            raise RuntimeExecutionError("timed_out", "Execution deadline elapsed during startup")
        if version != s.expected_pi_version:
            raise RuntimeExecutionError("runtime_not_ready", "Pi version does not match the reviewed pin")
        self._diagnostic_stage = 'build_command'
        if s.compact_events:
            # The fixed image-owned preload runs inside the same isolated child;
            # no NODE_OPTIONS or job-selected loader path is used.
            command[1:1] = ["--import", COMPACTOR_PATH.resolve().as_uri()]
        command += ["--print", "--mode", "json", "--no-session", "--no-extensions", "--no-skills",
                    "--no-prompt-templates", "--no-themes", "--no-context-files", "--offline",
                    "--provider", s.provider, "--model", s.model]
        for extension in getattr(profile, 'extension_paths', ()):
            # Fixed profile-owned immutable image resources, never a Job path.
            command += ['--extension', str(extension.resolve())]
        allowed_tools = getattr(profile, 'allowed_tools', None)
        if allowed_tools is not None:
            command += ['--tools', ','.join(allowed_tools)]
        for path in (s.skill_paths if getattr(profile, 'load_skills', True) else ()):
            command += ["--skill", str(path.resolve())]
        # Trusted profiles provide domain instructions, never request-selected code.
        self._diagnostic_stage = 'build_prompt'
        try:
            prompt = profile.build_prompt(wire)
        except ImportError:
            raise RuntimeExecutionError("runtime_not_ready", "Reviewed task prompt builder is unavailable")
        self._diagnostic_stage = 'write_prompt'
        prompt_path = workspace / "task.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        command += ["@" + str(prompt_path)]
        group = config = journal = job_object = process = None
        if not s.fixture_mode:
            self._diagnostic_stage = 'prepare_isolation'
            group, config, command = self._prepare_cgroup(workspace, command, uid)
        self._diagnostic_stage = 'persist_journal'
        control = s.control_dir or home
        control.mkdir(parents=True, exist_ok=True)
        journal = control / ("runtime-" + uuid.uuid4().hex + ".json")
        self._atomic(journal, json.dumps({"kind": "cgroup" if group else ("fixture" if s.fixture_mode else "uid"),
                                        "uid": uid, "cgroup": str(group) if group else None}))
        events = queue.Queue(maxsize=128)
        threads = []
        stopped = False
        failure = None
        started = utc_now()
        observed_models = set()
        completed = False
        assistant_seen = False
        last_output = time.monotonic()
        total = 0
        output_accounting = {}
        root_exited_at = None

        def emit(kind, **values):
            try:
                sink({"type": kind, "timestamp": utc_now(), **values})
            except Exception as exc:
                raise RuntimeExecutionError("event_sink_error", "Runtime event consumer failed") from exc

        def reader(pipe, kind):
            try:
                while True:
                    data = pipe.readline(s.max_line_bytes + 1)
                    if not data:
                        break
                    while True:
                        try:
                            events.put((kind, data), timeout=0.05)
                            break
                        except queue.Full:
                            if stop_readers.is_set():
                                return
            finally:
                pipe.close()

        stop_readers = threading.Event()
        self._diagnostic_stage = 'launch'
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd=workspace, env=env,
                                       start_new_session=os.name != "nt", close_fds=True,
                                       creationflags=0x00000004 if os.name == "nt" else 0)  # CREATE_SUSPENDED
            if os.name == "nt":
                from .windows_job import WindowsJob
                job_object = WindowsJob(process)
                job_object.resume(process)
            for pipe, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                thread = threading.Thread(target=reader, args=(pipe, kind), daemon=True)
                thread.start()
                threads.append(thread)
            emit("runtime_started", fixture_mode=s.fixture_mode)
            self._diagnostic_stage = 'execute_native'
            while True:
                now = time.monotonic()
                if process.poll() is not None and root_exited_at is None:
                    root_exited_at = now
                if root_exited_at is not None and now - root_exited_at >= 0.2 and events.empty():
                    break
                if cancel.is_set():
                    raise RuntimeExecutionError("cancelled", "Execution cancelled")
                if now >= end_time:
                    raise RuntimeExecutionError("timed_out", "Execution deadline elapsed")
                if now - last_output > s.idle_timeout_seconds:
                    raise RuntimeExecutionError("runtime_idle_timeout", "Pi produced no events within the bounded idle interval", True)
                try:
                    kind, data = events.get(timeout=0.025)
                except queue.Empty:
                    if process.poll() is not None and not any(t.is_alive() for t in threads) and events.empty():
                        break
                    continue
                last_output = now
                total += len(data)
                if len(data) > s.max_line_bytes or total > s.max_output_bytes:
                    raise RuntimeExecutionError("runtime_output_limit", "Pi output limit exceeded")
                if kind == "stderr":
                    # Consume, bound and discard: tool/provider diagnostics can
                    # contain credentials or arbitrary third-party text.
                    continue
                try:
                    event = json.loads(data.decode("utf-8"))
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise ValueError()
                except (UnicodeError, ValueError) as exc:
                    raise RuntimeExecutionError("runtime_protocol_error", "Pi emitted malformed JSONL") from exc
                event_type = event["type"]
                if event_type == "runtime_output_stats":
                    values = {key: event.get(key) for key in OUTPUT_STAT_KEYS}
                    if (not s.compact_events or any(type(value) is not int or not 0 <= value <= 1024**3
                                                  for value in values.values())):
                        raise RuntimeExecutionError("runtime_protocol_error", "Invalid bounded output accounting")
                    output_accounting = values
                    continue
                if event_type == "runtime_stream_error":
                    code = event.get("code")
                    if not s.compact_events or code not in {"runtime_output_limit", "runtime_raw_output_limit",
                            "runtime_output_line_limit", "runtime_event_limit", "runtime_protocol_error"}:
                        code = "runtime_protocol_error"
                    raise RuntimeExecutionError(code, "Pinned Pi event transport exceeded its bound or protocol")
                if event_type == "runtime_launcher_error":
                    raise RuntimeExecutionError("runtime_not_ready", "Child privilege or isolation verification failed")
                if event_type in ("message_end", "turn_end"):
                    message = event.get("message", {})
                    if isinstance(message, dict) and message.get("role") == "assistant":
                        assistant_seen = True
                        model = message.get("model")
                        provider = message.get("provider")
                        if not isinstance(model, str) or not isinstance(provider, str):
                            raise RuntimeExecutionError("runtime_protocol_error", "Assistant event lacks actual provider/model")
                        observed_models.add((provider, model))
                        if (provider, model) != (s.provider, s.model):
                            raise RuntimeExecutionError("model_mismatch", "Pi selected a different provider or model")
                        if message.get("stopReason") in ("error", "aborted"):
                            raise self._model_request_error(message)
                if event_type == "agent_end":
                    completed = True
                # Emit metadata only, never prompts, args, message bodies, tool
                # results, session cwd, or untrusted progress percentages.
                if event_type in {"agent_start", "agent_end", "turn_start", "turn_end", "message_end", "tool_execution_start", "tool_execution_end"}:
                    emit("pi_event", event=event_type)
            self._diagnostic_stage = 'validate_completion'
            if process.returncode:
                raise RuntimeExecutionError("runtime_exit", "Pi exited unsuccessfully", True)
            if not completed or not assistant_seen or not observed_models:
                raise RuntimeExecutionError("runtime_protocol_error", "Pi exited without a complete assistant/agent event sequence")
        except BaseException as exc:
            failure = exc
        finally:
            if s.compact_events:
                output_accounting["physical_output_bytes"] = total
                # All values are controller counters or bounded integers. Never
                # log provider/model error text, prompt, tool data or credentials.
                record = {"event": "runtime_output_accounting", "task_id": checked.task_id,
                          **output_accounting}
                logging.getLogger(__name__).log(logging.WARNING if failure else logging.INFO,
                                               "%s", json.dumps(record, sort_keys=True))
            # Every path fences the process tree, even normal root exit with
            # lingering children. Returning/raising before confirmation is unsafe.
            try:
                if process:
                    self._stop_tree(process, group, job_object, None if s.fixture_mode else uid)
                stopped = True
            except BaseException as cleanup_error:
                self._diagnostic_stage = 'stop_processes'
                self._unsafe = True
                failure = RuntimeExecutionError("process_safety_unverified", "Runtime process tree stop could not be confirmed; recovery must block")
                failure.__cause__ = cleanup_error
            stop_readers.set()
            for thread in threads:
                thread.join(timeout=0.2)
            if stopped:
                if job_object:
                    job_object.close()
                if group:
                    group.rmdir()
                if config:
                    config.unlink(missing_ok=True)
                journal.unlink(missing_ok=True)
        if failure:
            if (isinstance(failure, RuntimeExecutionError) and failure.code == "model_http_transient"
                    and failure.retryable and job.get("attempt", 1) == 1):
                self._wait_for_model_retry(cancel, end_time)
            raise failure
        self._diagnostic_stage = 'write_provenance'
        finished = utc_now()
        provenance = {"model": s.model, "provider": s.provider, "pi_package": PI_PACKAGE,
                      "pi_version": version, "node_version": node, "image_digest": s.image_digest,
                      "skill_bundle_revision": s.skill_bundle_revision, "runtime_revision": s.runtime_revision,
                      "token_usage": None, "fixture_mode": s.fixture_mode,
                      **bounded_provenance(profile, started, finished)}
        if s.compact_events:
            provenance["output_accounting"] = output_accounting
        # Profile provenance is bounded controller metadata; model-supplied
        # provenance never supplies execution or acceptance authority.
        provenance_path = workspace / "provenance.json"
        if provenance_path.is_symlink():
            raise RuntimeExecutionError("unsafe_workspace", "Provenance output cannot be a symlink")
        self._atomic(provenance_path, json.dumps(provenance, ensure_ascii=False, sort_keys=True))
        emit("runtime_finished")

    @staticmethod
    def _fixture_group_processes(pgid):
        """Linux fixture evidence only; zombies cannot execute or fork."""
        matches = []
        for path in Path("/proc").iterdir():
            if not path.name.isdecimal():
                continue
            try:
                # comm may contain spaces or ')'; fields after its final ')'
                # begin with state, parent PID and process group ID.
                fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                state, group_id = fields[0], int(fields[2])
            except FileNotFoundError:
                continue  # Process was reaped between enumeration and read.
            except (OSError, ValueError, IndexError) as exc:
                raise RuntimeExecutionError("process_safety_unverified", "Cannot verify fixture process group state") from exc
            if group_id == pgid and state not in {"Z", "X"}:
                matches.append(int(path.name))
        return matches

    def _stop_fixture_group(self, pgid):
        # killpg queues signals asynchronously; waiting for an already-exited
        # root process does not establish that its orphaned children stopped.
        deadline = time.monotonic() + self.settings.kill_grace_seconds
        empty_scans = 0
        while time.monotonic() < deadline:
            if self._fixture_group_processes(pgid):
                empty_scans = 0
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                empty_scans += 1
                if empty_scans >= 2:
                    return
            time.sleep(0.02)
        raise RuntimeExecutionError("process_safety_unverified", "Fixture process group stop could not be confirmed")

    def _stop_tree(self, process, group, job_object, uid=None):
        s = self.settings
        if group:
            # Send TERM to every current member, then cgroup.kill catches fork
            # races, detached tool processes and different-UID descendants.
            for pid in (group / "cgroup.procs").read_text().split():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            until = time.monotonic() + s.term_grace_seconds
            while self._cgroup_alive(group) and time.monotonic() < until:
                time.sleep(0.02)
            self._kill_cgroup(group)
        elif job_object:
            if job_object.alive():
                job_object.kill()
            until = time.monotonic() + s.kill_grace_seconds
            while job_object.alive() and time.monotonic() < until:
                time.sleep(0.02)
            if job_object.alive():
                raise RuntimeExecutionError("process_safety_unverified", "Fixture Job Object is still populated")
        elif os.name == "nt":
            process.kill()
            process.wait(timeout=s.kill_grace_seconds)
            raise RuntimeExecutionError("process_safety_unverified", "Fixture Job Object assignment failed; descendant safety is unknown")
        else:
            # POSIX fixture only: descendants which deliberately setsid can
            # escape. This path is never accepted as production isolation.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=s.term_grace_seconds)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if s.fixture_mode and sys.platform == "linux":
                self._stop_fixture_group(process.pid)
        try:
            process.wait(timeout=s.kill_grace_seconds)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeExecutionError("process_safety_unverified", "Pi root process did not stop") from exc
        if uid is not None:
            self._stop_uid(uid)
