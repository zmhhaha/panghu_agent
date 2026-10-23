"""Run one authorized sample phase on a local ARM64 Linux Docker host.

This command DOES call the real model when run. It only produces candidates;
completed execution is never publication or independent artifact acceptance.
The upstream env file is passed directly to Docker and is never read here.
Checkpoint argument is an earlier attempt directory containing home/project.
Requires a trusted local Docker daemon and either host root or UID 10000.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

PYTHON = "/opt/hermes/.venv/bin/python"
UID = 10000
PHASES = {"plan_initial", "plan_options", "prepare", "generate", "revise"}
SKILL_TARGET = "/opt/ppt-master/skills/ppt-master"

# This runs before importing the worker, and before any possible model request.
# Docker's embedded resolver may resolve the alias but must not resolve public
# names through a host resolver. --dns 127.0.0.1 supplies no external resolver.
WORKER_BOOT = r'''
import ipaddress, json, os, pathlib, socket, sys
expected = os.environ['PPT_EXPECTED_PROXY_IP']
report = {'schema_version': 1, 'passed': False,
          'host_metadata_tcp_denial': 'pending'}
try:
    defaults = []
    for line in pathlib.Path('/proc/net/route').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) > 7 and fields[1] == '00000000' and fields[7] == '00000000' and int(fields[3], 16) & 1:
            defaults.append('ipv4')
    v6 = pathlib.Path('/proc/net/ipv6_route')
    if v6.exists():
        for line in v6.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 10 and fields[0] == '0' * 32 and fields[1] == '00' and int(fields[8], 16) & 1 and not int(fields[8], 16) & 0x200:
                defaults.append('ipv6')
    if defaults:
        raise RuntimeError('external_default_route')
    addresses = {item[4][0] for item in socket.getaddrinfo('model-proxy', 8080, type=socket.SOCK_STREAM)}
    if addresses != {expected}:
        raise RuntimeError('proxy_dns_mismatch')
    # Fail closed if embedded Docker DNS forwards to an external resolver.
    try:
        socket.getaddrinfo('example.com', 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        pass
    else:
        raise RuntimeError('external_dns_resolution_enabled')
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://model-proxy:8080/healthz', timeout=5) as response:
        if response.status != 200 or json.load(response) != {'status': 'ok'}:
            raise RuntimeError('proxy_not_ready')
    # Test the host gateway, the explicitly identified host SSH service, and
    # metadata before any worker/model code can execute. Every unexpected
    # successful connection aborts; infrastructure errors also fail closed.
    targets = json.loads(os.environ['PPT_DENIED_TARGETS'])
    for host, port in targets:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            sock.connect((host, port))
        except (ConnectionRefusedError, TimeoutError, OSError):
            pass
        else:
            raise RuntimeError('forbidden_tcp_connection_succeeded')
        finally:
            sock.close()
    report['host_metadata_tcp_denial'] = 'passed'
    # Ensure supplied inputs really are readable by the non-root worker.
    for directory, dirs, files in os.walk('/work/inputs'):
        for name in files:
            with open(os.path.join(directory, name), 'rb') as source:
                source.read(1)
    report.update(passed=True, no_default_route=True, proxy_dns_exact=True,
                  external_dns_blocked=True, proxy_healthy=True)
except Exception as exc:
    report['error_type'] = type(exc).__name__
finally:
    pathlib.Path('/work/result/network-preflight.json').write_text(json.dumps(report))
if not report['passed']:
    sys.exit(70)
# Never inherit arbitrary image authentication or platform environment variables.
allow = {'PATH', 'PYTHONPATH', 'HOME', 'HERMES_HOME', 'HERMES_WRITE_SAFE_ROOT',
         'HERMES_LAZY_INSTALL_TARGET', 'PLAYWRIGHT_BROWSERS_PATH',
         'PPT_MASTER_SKILL_DIR', 'PPT_MASTER_COMMIT', 'PYTHONDONTWRITEBYTECODE',
         'PYTHONUNBUFFERED', 'PIP_DISABLE_PIP_VERSION_CHECK', 'LANG', 'LC_ALL',
         'PPT_MODEL', 'PPT_MODEL_API_KEY', 'PPT_MODEL_BASE_URL',
         'PPT_MODEL_PROXY_ORIGIN'}
env = {key: value for key, value in os.environ.items() if key in allow}
os.execve(sys.executable, [sys.executable, '-m', 'ppt_service.worker.execute',
                          '--task', '/work/task.json', '--deadline', sys.argv[1]], env)
'''


class RunError(Exception):
    """Messages are fixed codes, never subprocess stderr or credential values."""


def private_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def absolute_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or any(part in {"..", "."} for part in path.parts):
        raise RunError("explicit_absolute_path_required")
    # Do not resolve links away before checking every existing ancestor.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RunError("symlink_path_rejected")
    if "," in str(path) or "\n" in str(path) or "\r" in str(path):
        raise RunError("unsupported_mount_path")
    return path


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RunError("ordinary_unlinked_file_required")
    return info


def scan_tree(root: Path, *, skill_exception: bool = False) -> None:
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise RunError("ordinary_directory_required")
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if (skill_exception and path == root / "skills" / "ppt-master"
                        and os.readlink(path) == SKILL_TARGET):
                    continue
                raise RunError("checkpoint_or_input_link_rejected")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise RunError("special_file_rejected")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise RunError("hardlink_rejected")


def read_object(path: Path, limit: int = 1_048_576) -> dict:
    if regular(path).st_size > limit:
        raise RunError("json_document_too_large")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RunError("duplicate_json_key")
            result[key] = value
        return result
    def constant(value):
        raise RunError("invalid_json_constant")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                       parse_constant=constant)
    if not isinstance(value, dict):
        raise RunError("json_object_required")
    return value


def validate_task(task: dict) -> None:
    if not isinstance(task.get("phase"), str) or task["phase"] not in PHASES:
        raise RunError("invalid_phase")
    for name in ("job_id", "session_id"):
        if not isinstance(task.get(name), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task[name]):
            raise RunError("invalid_task_identifier")
    if type(task.get("attempt")) is not int or not 1 <= task["attempt"] <= 100:
        raise RunError("invalid_attempt")
    if not isinstance(task.get("input_snapshot_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", task["input_snapshot_sha256"]):
        raise RunError("invalid_input_snapshot")
    requirements = task.get("requirements", {})
    if not isinstance(requirements, dict) or not requirements.get("external_model_acknowledged"):
        raise RunError("external_model_acknowledgement_required")
    pages = requirements.get("pages", 8)
    if type(pages) is not int or not 3 <= pages <= 20:
        raise RunError("invalid_slide_count")
    confirmation = {"plan_options": "stage1_confirmation", "prepare": "stage2_confirmation"}.get(task["phase"])
    if confirmation and (not isinstance(task.get(confirmation), dict) or not task[confirmation]):
        raise RunError("explicit_confirmation_required")


def sample_deadline(phase: str, override: int | None = None) -> int:
    """An explicit operator override never removes the phase's hard deadline."""
    if phase not in PHASES:
        raise RunError("invalid_phase")
    planning = phase in {"plan_initial", "plan_options"}
    default = 600 if planning else 1800
    if override is None:
        return default
    maximum = 600 if planning else 3600
    if type(override) is not int or not 30 <= override <= maximum:
        raise RunError("sample_deadline_out_of_range")
    return override


class Docker:
    def __init__(self, logs: Path):
        # Force the local socket; never honor a remote Docker context or TLS env.
        self.prefix = ["docker", "--host", "unix:///var/run/docker.sock"]
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": str(logs.parent / "docker-config"),
                    "DOCKER_CONFIG": str(logs.parent / "docker-config")}
        self.logs = logs
        self.sequence = 0

    def call(self, *args: str, timeout: int = 60, check: bool = True) -> str:
        self.sequence += 1
        # Arguments (including paths) and raw Docker responses are never printed.
        with (self.logs / f"docker-{self.sequence:03d}.private.log").open("wb") as log:
            result = subprocess.run(self.prefix + list(args), env=self.env,
                                    stdout=subprocess.PIPE, stderr=log, timeout=timeout,
                                    check=False)
        if check and result.returncode:
            raise RunError("docker_command_failed")
        return result.stdout.decode("utf-8", errors="strict").strip()

    def state(self, container: str) -> dict:
        value = json.loads(self.call("container", "inspect", "--format", "{{json .State}}", container))
        if not isinstance(value, dict) or type(value.get("Running")) is not bool:
            raise RunError("invalid_container_state")
        return value

    def stop(self, container: str) -> bool:
        try:
            self.call("stop", "--time", "20", container, timeout=35, check=False)
            if self.state(container)["Running"]:
                self.call("kill", container, timeout=15, check=False)
            return self.state(container)["Running"] is False
        except (RunError, OSError, ValueError, subprocess.SubprocessError):
            return False


class Firewall:
    """Only add/remove this attempt's exact source-IP rules; never flush."""

    def __init__(self, logs: Path, worker_ip: str, proxy_ip: str, bridge: str, run_id: str):
        self.logs = logs
        self.rules = []
        self.sequence = 0
        match = ["-i", bridge, "-s", worker_ip + "/32", "-m", "comment",
                 "--comment", "ppt-sample-" + run_id]
        self.planned = [
            ("INPUT", match + ["-j", "REJECT"]),
            ("DOCKER-USER", match + ["-j", "REJECT"]),
            ("DOCKER-USER", match + ["-d", proxy_ip + "/32", "-p", "tcp",
                                    "--dport", "8080", "-j", "ACCEPT"]),
        ]

    def command(self, *args: str) -> None:
        self.sequence += 1
        with (self.logs / f"firewall-{self.sequence:03d}.private.log").open("wb") as log:
            result = subprocess.run(["iptables", "-w", "10", *args],
                                    stdout=log, stderr=subprocess.STDOUT,
                                    timeout=15, check=False)
        if result.returncode:
            raise RunError("scoped_firewall_command_failed")

    def install(self) -> None:
        # Same-bridge traffic must traverse iptables; absence is not accepted.
        setting = Path("/proc/sys/net/bridge/bridge-nf-call-iptables")
        if setting.read_text().strip() != "1":
            raise RunError("bridge_netfilter_required")
        self.command("-S", "DOCKER-USER")
        for chain, rule in self.planned:
            # Track before mutating so an interrupted command is still cleaned.
            self.rules.append((chain, rule))
            self.command("-I", chain, "1", *rule)
            self.command("-C", chain, *rule)

    def remove(self) -> bool:
        remaining = []
        for chain, rule in reversed(self.rules):
            try:
                self.command("-D", chain, *rule)
            except (RunError, OSError, subprocess.SubprocessError):
                remaining.append((chain, rule))
        self.rules = list(reversed(remaining))
        return not self.rules


def continuation_request(task: dict, enabled: bool) -> dict | None:
    """CLI consent and a controller-owned oracle are both mandatory.

    This is a private working handoff, never an accepted version/base. The
    operator must separately obtain authorization for another paid model run.
    """
    if not enabled:
        if "continuation" in task:
            raise RunError("continuation_cli_flag_required")
        return None
    value = task.get("continuation")
    keys = {"kind", "source_manifest_sha256", "approved_spec_sha256",
            "approved_lock_sha256", "resolution"}
    if not isinstance(value, dict) or set(value) != keys:
        raise RunError("continuation_metadata_required")
    if (value["kind"] != "needs_input"
            or value["resolution"] != "worker_supervised_official_preview"):
        raise RunError("continuation_kind_or_resolution_rejected")
    for key in ("source_manifest_sha256", "approved_spec_sha256", "approved_lock_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise RunError("continuation_invalid_sha256")
    if task.get("phase") != "generate" or "base_version_id" in task:
        raise RunError("continuation_private_generate_only")
    return value


def continuation_inputs(inputs: Path, task: dict) -> None:
    """Recheck actual read-only inputs with validate_sample's trusted asset rules."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ppt_service.scripts.validate_sample import Rejected, inputs_verified
    try:
        inputs_verified(inputs, task)
    except Rejected as exc:
        raise RunError("continuation_input_integrity_rejected") from exc


def continuation_stopped(docker: Docker, source: Path, proof: dict) -> None:
    """Inspect both real retained containers, not their saved state reports."""
    worker = proof["worker_container"]
    proxy = proof.get("proxy_container")
    if proxy != worker.removesuffix("-worker") + "-proxy":
        raise RunError("continuation_proxy_identity_mismatch")
    if type(proof.get("worker_exit_code")) is not int or proof["worker_exit_code"] != 0:
        raise RunError("continuation_worker_exit_proof_required")
    for container, parts in ((worker, {"/work/" + name: name for name in ("home", "project", "result")}),
                             (proxy, {"/audit": "audit"})):
        state = docker.state(container)
        if (state.get("Running") is not False or state.get("Status") != "exited"
                or type(state.get("ExitCode")) is not int or state["ExitCode"] != 0
                or any(state.get(key) for key in ("OOMKilled", "Dead", "Restarting", "Paused", "Error"))):
            raise RunError("continuation_container_not_successfully_exited")
        mounts = json.loads(docker.call("container", "inspect", "--format", "{{json .Mounts}}", container))
        if not isinstance(mounts, list) or not all(isinstance(m, dict) for m in mounts):
            raise RunError("continuation_mount_identity_mismatch")
        for destination, name in parts.items():
            matches = [m for m in mounts if m.get("Destination") == destination]
            if (len(matches) != 1 or matches[0].get("Type") != "bind"
                    or matches[0].get("Source") != str(source / name)):
                raise RunError("continuation_mount_identity_mismatch")


def validate_continuation(source: Path, task: dict, previous: dict,
                          manifest: dict, oracle: dict) -> None:
    # Call the same complete integrity validator as validate_sample. Its report
    # references are hash checked; upstream quality claims remain untrusted.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ppt_service.worker.candidate_validation import ValidationError, validate_manifest

    if (type(previous.get("attempt")) is not int or type(task.get("attempt")) is not int
            or not 1 <= previous["attempt"] < task["attempt"] <= 100):
        raise RunError("continuation_new_attempt_required")
    for key in ("stage1_confirmation", "stage2_confirmation"):
        if (not isinstance(previous.get(key), dict) or not previous[key]
                or task.get(key) != previous[key]):
            raise RunError("continuation_confirmation_mismatch")
    # Identity hashes alone must not disguise a different trusted asset list.
    if task.get("assets") != previous.get("assets"):
        raise RunError("continuation_assets_mismatch")
    questions = manifest.get("questions")
    if (not isinstance(questions, list) or not questions
            or not all(isinstance(q, str) and q.strip() for q in questions)):
        raise RunError("continuation_questions_required")
    if hashlib.sha256((source / "result" / "manifest.json").read_bytes()).hexdigest() != oracle["source_manifest_sha256"]:
        raise RunError("continuation_manifest_hash_mismatch")
    try:
        validate_manifest(source / "project", manifest)
    except ValidationError as exc:
        raise RunError("continuation_manifest_integrity_rejected") from exc
    project = source / "project" / manifest["project_root"]
    artifacts = {a["path"]: a for a in manifest["artifacts"]}
    for name, key in (("design_spec.md", "approved_spec_sha256"), ("spec_lock.md", "approved_lock_sha256")):
        artifact = artifacts.get(name)
        if artifact is None or artifact["sha256"] != oracle[key]:
            raise RunError("continuation_approved_spec_lock_mismatch")
    # Reject even an undeclared/exported deck anywhere in the copied workspace.
    if (any(a["role"].casefold() == "pptx" or "presentationml.presentation" in a["media_type"].lower()
            for a in artifacts.values())
            or any(p.suffix.lower() == ".pptx" for p in (source / "project").rglob("*"))):
        raise RunError("continuation_pptx_rejected")
    # The validator has checked these file bytes against the declarations above.
    # Keep the canonical files rooted at the declared split project, not aliases.
    for name in ("design_spec.md", "spec_lock.md"):
        regular(project / name)


def checkpoint(docker: Docker, source: Path, task: dict, attempt: Path, *,
               continue_needs_input: bool = False) -> dict | None:
    oracle = continuation_request(task, continue_needs_input)
    if oracle is not None:
        absolute_path(str(source))
        scan_tree(source / "result")
    proof = read_object(source / "run-state.json")
    container = proof.get("worker_container", "")
    if not isinstance(container, str) or not re.fullmatch(r"ppt-sample-[0-9a-f]{32}-worker", container):
        raise RunError("checkpoint_container_identity_missing")
    if (proof.get("status") != "candidate_execution_finished"
            or proof.get("worker_stopped") is not True or proof.get("proxy_stopped") is not True
            or proof.get("job_id") != task["job_id"]):
        raise RunError("checkpoint_stopped_success_proof_required")
    if proof.get("input_snapshot_sha256") != task["input_snapshot_sha256"]:
        raise RunError("checkpoint_snapshot_mismatch")
    previous = read_object(source / "task.json")
    for field in ("job_id", "session_id", "input_snapshot_sha256"):
        if field not in previous or previous[field] != task.get(field):
            raise RunError("checkpoint_task_identity_mismatch")
    for field in ("job_id", "attempt", "phase", "input_snapshot_sha256"):
        if field not in previous or proof.get(field) != previous[field]:
            raise RunError("checkpoint_run_state_task_mismatch")
    phase = task["phase"]
    predecessors = {"plan_options": {"plan_initial"}, "prepare": {"plan_options"},
                    "generate": {"prepare"}, "revise": {"generate", "revise"}}
    if oracle is not None:
        if previous.get("phase") != "generate" or phase != "generate":
            raise RunError("continuation_generate_transition_required")
    elif previous.get("phase") not in predecessors.get(phase, set()):
        raise RunError("checkpoint_phase_transition_rejected")
    if phase == "revise" and (not isinstance(task.get("base_version_id"), str)
                              or not task["base_version_id"].strip()):
        # Presence is checked here; the caller must verify the version-to-checkpoint mapping.
        raise RunError("checkpoint_base_version_required")
    execution = read_object(source / "result" / "execution.json")
    if execution.get("status") != "completed" or execution.get("gateway_stopped") is not True:
        raise RunError("checkpoint_completed_execution_required")
    for field in ("job_id", "attempt", "session_id", "phase"):
        if execution.get(field) != previous.get(field):
            raise RunError("checkpoint_execution_task_mismatch")
    manifest_path = source / "result" / "manifest.json"
    manifest = read_object(manifest_path, limit=4 * 1024 * 1024)
    expected_status = {"plan_initial": "awaiting_confirmation", "plan_options": "awaiting_confirmation",
                       "prepare": "prepared", "generate": "candidate", "revise": "candidate"}
    if (manifest.get("phase") != previous["phase"]
            or manifest.get("input_snapshot_sha256") != previous["input_snapshot_sha256"]
            or manifest.get("status") != ("needs_input" if oracle is not None else expected_status[previous["phase"]])):
        raise RunError("checkpoint_manifest_stage_mismatch")
    confirmation_key = {"plan_options": "stage1_confirmation", "prepare": "stage2_confirmation"}.get(phase)
    confirmation = task.get(confirmation_key) if confirmation_key else None
    if isinstance(confirmation, dict) and "source_manifest_sha256" in confirmation:
        if confirmation["source_manifest_sha256"] != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
            raise RunError("checkpoint_confirmation_manifest_mismatch")
    if oracle is not None:
        continuation_stopped(docker, source, proof)
    if docker.state(container)["Running"] is not False:
        raise RunError("checkpoint_source_is_active")
    mounts = json.loads(docker.call("container", "inspect", "--format", "{{json .Mounts}}", container))
    for name in ("home", "project"):
        expected = str(source / name)
        if not any(m.get("Type") == "bind" and m.get("Source") == expected
                   and m.get("Destination") == f"/work/{name}" for m in mounts):
            raise RunError("checkpoint_mount_identity_mismatch")
        scan_tree(source / name, skill_exception=name == "home")
    if oracle is not None:
        validate_continuation(source, task, previous, manifest, oracle)
        continuation_stopped(docker, source, proof)
    # copytree never follows a link: only this exact immutable-image skill link
    # is omitted. configure() recreates it using the new image's official path.
    def ignore(directory, names):
        path = Path(directory) / "ppt-master"
        if Path(directory) == source / "home" / "skills" and path.is_symlink():
            if os.readlink(path) != SKILL_TARGET:
                raise RunError("unexpected_skill_link")
            return ["ppt-master"]
        return []
    for name in ("home", "project"):
        shutil.copytree(source / name, attempt / name, symlinks=True,
                        ignore=ignore if name == "home" else None)
        scan_tree(attempt / name)
    if docker.state(container)["Running"] is not False:
        raise RunError("checkpoint_became_active")
    if oracle is not None:
        continuation_stopped(docker, source, proof)
        return {"scope": "private_needs_input_continuation",
                "source_attempt": previous["attempt"], "source_attempt_dir": str(source),
                "source_manifest_sha256": oracle["source_manifest_sha256"]}
    return None


def own_tree(path: Path) -> None:
    for directory, dirs, files in os.walk(path):
        os.chmod(directory, 0o700)
        if os.geteuid() == 0:
            os.chown(directory, UID, UID)
        for name in files:
            file = Path(directory) / name
            info = regular(file)
            os.chmod(file, 0o700 if info.st_mode & 0o111 else 0o600)
            if os.geteuid() == 0:
                os.chown(file, UID, UID)


def mount(source: Path, destination: str, readonly: bool = False) -> list[str]:
    return ["--mount", f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")]


def run(args: argparse.Namespace) -> tuple[dict, int]:
    os.umask(0o077)
    if platform.system() != "Linux" or platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RunError("arm64_linux_host_required")
    if os.geteuid() not in {0, UID}:
        raise RunError("host_root_or_uid_10000_required")
    upstream_ip = ipaddress.ip_address(args.upstream_ip)
    if upstream_ip.version != 4 or not upstream_ip.is_global:
        raise RunError("upstream_public_ipv4_required")
    root, task_path, upstream = map(absolute_path, (args.root, args.task, args.upstream_env))
    if not root.is_dir() or root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise RunError("root_must_be_existing_owned_private_directory")
    regular(task_path)
    env_info = regular(upstream)  # Metadata only; DO NOT open or parse this file.
    if env_info.st_uid != os.geteuid() or stat.S_IMODE(env_info.st_mode) & 0o077:
        raise RunError("upstream_env_must_be_owned_private_file")
    inputs = root / "inputs"
    scan_tree(inputs)
    if upstream.is_relative_to(inputs):
        raise RunError("upstream_env_inside_worker_inputs")
    image = args.image
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", image)
            or ("@sha256:" not in image and ":" not in image.rsplit("/", 1)[-1])
            or ("@" in image and not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image))):
        raise RunError("explicit_image_tag_or_digest_required")
    if upstream == task_path:
        raise RunError("upstream_env_cannot_be_task")
    task = read_object(task_path)
    validate_task(task)
    continue_needs_input = getattr(args, "continue_needs_input", False)
    continuation_request(task, continue_needs_input)
    if continue_needs_input:
        continuation_inputs(inputs, task)
    deadline = sample_deadline(task["phase"], getattr(args, "deadline_seconds", None))
    source = absolute_path(args.checkpoint) if args.checkpoint else None
    if task["phase"] != "plan_initial" and source is None:
        raise RunError("checkpoint_required_for_later_phase")
    if source and upstream.is_relative_to(source):
        raise RunError("upstream_env_inside_checkpoint")
    run_id = uuid.uuid4().hex
    attempt = root / f"attempt-{run_id}"
    attempt.mkdir(mode=0o700)
    for name in ("logs", "audit", "control", "docker-config", "result"):
        (attempt / name).mkdir(mode=0o700)
    docker = Docker(attempt / "logs")
    state = {"schema_version": 1, "status": "starting", "attempt_dir": str(attempt),
             "job_id": task["job_id"], "attempt": task["attempt"], "phase": task["phase"],
             "input_snapshot_sha256": task["input_snapshot_sha256"],
             "image": image, "worker_stopped": False, "proxy_stopped": False,
             "delivery_published": False, "artifact_acceptance": "not_performed",
             "host_metadata_tcp_denial": "not_tested"}
    worker = f"ppt-sample-{run_id}-worker"
    proxy = f"ppt-sample-{run_id}-proxy"
    network = f"ppt-sample-{run_id}-internal"
    state.update(worker_container=worker, proxy_container=proxy, internal_network=network)
    created = []
    network_created = False
    firewall = None
    returncode = 1
    previous_handlers = {}
    def interrupted(signum, frame):
        raise RunError("interrupted")
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, interrupted)
    try:
        private_json(attempt / "run-state.json", state)
        info = json.loads(docker.call("info", "--format", '{{json .}}'))
        if info.get("OSType") != "linux" or info.get("Architecture") not in {"aarch64", "arm64"}:
            raise RunError("arm64_linux_docker_required")
        # Require the authorized image to be present. Never pull implicitly.
        image_id = docker.call("image", "inspect", "--format", "{{.Id}}", image)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RunError("invalid_local_image_id")
        state["image_id"] = image_id
        if source:
            restored = checkpoint(docker, source, task, attempt,
                                  continue_needs_input=continue_needs_input)
            if restored is not None:
                state.update(restored)
        else:
            for name in ("home", "project"):
                (attempt / name).mkdir(mode=0o700)
        for name in ("home", "project", "result", "audit"):
            own_tree(attempt / name)
        private_json(attempt / "task.json", task)
        os.chmod(attempt / "task.json", 0o444)
        token = secrets.token_urlsafe(48)
        proxy_env = attempt / "control" / "proxy.env"
        worker_env = attempt / "control" / "worker.env"
        proxy_env.write_text(f"PPT_PROXY_CLIENT_KEY={token}\n", encoding="ascii")
        worker_env.write_text(f"PPT_MODEL_API_KEY={token}\n", encoding="ascii")
        os.chmod(proxy_env, 0o600)
        os.chmod(worker_env, 0o600)
        del token
        docker.call("network", "create", "--driver", "bridge", "--internal", network)
        network_created = True
        net = json.loads(docker.call("network", "inspect", "--format", "{{json .}}", network))
        if net.get("Internal") is not True:
            raise RunError("network_is_not_internal")
        ipv4 = next((item for item in net["IPAM"]["Config"] if ":" not in item["Subnet"]), None)
        if not ipv4:
            raise RunError("internal_ipv4_subnet_required")
        subnet = ipaddress.ip_network(ipv4["Subnet"])
        proxy_ip = str(subnet.network_address + 2)
        if proxy_ip == ipv4.get("Gateway") or ipaddress.ip_address(proxy_ip) not in subnet.hosts():
            raise RunError("invalid_proxy_internal_ip")
        worker_ip = str(subnet.network_address + 3)
        if worker_ip == ipv4.get("Gateway") or ipaddress.ip_address(worker_ip) not in subnet.hosts():
            raise RunError("invalid_worker_internal_ip")
        bridge = net.get("Options", {}).get("com.docker.network.bridge.name") or "br-" + net["Id"][:12]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", bridge):
            raise RunError("invalid_bridge_interface")
        state.update(worker_ip=worker_ip, proxy_ip=proxy_ip, bridge_interface=bridge,
                     firewall_installed=False, firewall_removed=False)
        firewall = Firewall(attempt / "logs", worker_ip, proxy_ip, bridge, run_id)
        state["firewall_rules"] = [{"chain": chain, "rule": rule} for chain, rule in firewall.planned]
        common = ["--platform", "linux/arm64", "--user", f"{UID}:{UID}",
                  "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                  "--pids-limit", "512", "--init", "--restart", "no",
                  "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
                  "--log-driver", "local", "--log-opt", "max-size=20m", "--log-opt", "max-file=3"]
        docker.call("create", "--name", proxy, *common,
                    "--network", network, "--network-alias", "model-proxy", "--ip", proxy_ip,
                    "--env-file", str(upstream), "--env-file", str(proxy_env),
                    "--add-host", f"custom-llm-proxy.panghuer.top:{upstream_ip}",
                    "--env", "PPT_UPSTREAM_BASE_URL=https://custom-llm-proxy.panghuer.top/v1",
                    "--env", f"PPT_PROXY_HOST={proxy_ip}", "--env", "PPT_PROXY_PORT=8080",
                    "--env", "PPT_MODEL=gpt-6.1-sol",
                    "--env", "PPT_PROXY_USAGE_PATH=/audit/usage.jsonl",
                    "--env", f"PPT_USAGE_JOB_ID={task['job_id']}",
                    "--env", f"PPT_USAGE_ATTEMPT={task['attempt']}",
                    "--env", f"PPT_USAGE_PHASE={task['phase']}",
                    *mount(attempt / "audit", "/audit"),
                    "--workdir", "/tmp", "--entrypoint", PYTHON, image_id,
                    "-m", "ppt_service.worker.model_proxy")
        created.append(proxy)
        docker.call("network", "connect", "bridge", proxy)
        docker.call("start", proxy)
        health = ("import json,urllib.request; "
                  "o=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
                  f"r=o.open('http://{proxy_ip}:8080/healthz',timeout=2); "
                  "assert r.status==200 and json.load(r)=={'status':'ok'}")
        healthy = False
        for _ in range(30):
            if not docker.state(proxy)["Running"]:
                break
            try:
                docker.call("exec", proxy, PYTHON, "-c", health, timeout=5)
                healthy = True
                break
            except RunError:
                time.sleep(1)
        if not healthy:
            raise RunError("proxy_readiness_failed")
        docker.call("create", "--name", worker, *common,
                    "--network", network, "--ip", worker_ip, "--dns", "127.0.0.1",
                    "--add-host", f"model-proxy:{proxy_ip}",
                    "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
                    "--env-file", str(worker_env),
                    "--env", "PPT_UPSTREAM_API_KEY=", "--env", "PPT_PROXY_CLIENT_KEY=",
                    "--env", "PPT_MODEL=gpt-6.1-sol",
                    "--env", "PPT_MODEL_BASE_URL=http://model-proxy:8080/v1",
                    "--env", "PPT_MODEL_PROXY_ORIGIN=http://model-proxy:8080",
                    "--env", f"PPT_EXPECTED_PROXY_IP={proxy_ip}",
                    "--env", "PPT_DENIED_TARGETS=" + json.dumps(
                        [[ipv4["Gateway"], port] for port in (22, 80, 443, 2375, 2376, 8642, 19387)]
                        + [["192.168.137.101", 22], ["169.254.169.254", 80], ["169.254.169.254", 443]],
                        separators=(",", ":")),
                    *mount(inputs, "/work/inputs", True),
                    *mount(attempt / "home", "/work/home"),
                    *mount(attempt / "project", "/work/project"),
                    *mount(attempt / "result", "/work/result"),
                    *mount(attempt / "task.json", "/work/task.json", True),
                    "--workdir", "/work/project", "--entrypoint", PYTHON, image_id,
                    "-c", WORKER_BOOT, str(deadline))
        created.append(worker)
        # The controller also verifies Docker's topology before the process runs.
        nets = json.loads(docker.call("container", "inspect", "--format", "{{json .NetworkSettings.Networks}}", worker))
        if set(nets) != {network}:
            raise RunError("worker_has_unexpected_network")
        firewall.install()
        state["firewall_installed"] = True
        state.update(status="running", deadline_seconds=deadline)
        private_json(attempt / "run-state.json", state)
        docker.call("start", worker)
        # A hard controller limit also bounds a stuck bootstrap/gateway teardown.
        exit_text = docker.call("wait", worker, timeout=deadline + 120)
        if not re.fullmatch(r"[0-9]{1,3}", exit_text):
            raise RunError("invalid_worker_exit_status")
        state["worker_exit_code"] = int(exit_text)
        state["status"] = "candidate_execution_finished" if int(exit_text) == 0 else "worker_failed"
        returncode = 0 if int(exit_text) == 0 else 2
    except subprocess.TimeoutExpired:
        state.update(status="deadline_exceeded", error_code="controller_timeout")
    except Exception as exc:
        state.update(status="failed", error_code=str(exc) if isinstance(exc, RunError) else type(exc).__name__)
    finally:
        # Ignore a repeated terminal signal while making all writers stop.
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        for container, name in ((worker, "worker"), (proxy, "proxy")):
            if container not in created:
                state[f"{name}_stopped"] = True
                continue
            stopped = docker.stop(container)
            state[f"{name}_stopped"] = stopped
            try:
                private_json(attempt / f"{name}-state.json", docker.state(container))
                with (attempt / "logs" / f"{name}.private.log").open("wb") as log:
                    subprocess.run(docker.prefix + ["logs", container], env=docker.env,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=30, check=False)
            except (RunError, OSError, ValueError, subprocess.SubprocessError):
                state[f"{name}_evidence_error"] = True
        if not state["worker_stopped"] or not state["proxy_stopped"]:
            state["status"] = "cleanup_failed"
            returncode = 1
        if firewall is not None and state["worker_stopped"] and state["proxy_stopped"]:
            state["firewall_removed"] = firewall.remove()
            if not state["firewall_removed"]:
                state["status"] = "cleanup_failed"
                returncode = 1
        if worker in created and state["worker_stopped"]:
            try:
                report = read_object(attempt / "result" / "network-preflight.json")
                state["network_preflight_passed"] = report.get("passed") is True
                state["host_metadata_tcp_denial"] = report.get("host_metadata_tcp_denial", "not_tested")
                if report.get("passed") is not True:
                    returncode = 2
                    state["status"] = "network_preflight_failed"
            except (RunError, OSError, ValueError):
                state["network_preflight_passed"] = False
                if returncode == 0:
                    returncode = 2
                    state["status"] = "network_evidence_missing"
        if network_created and state["worker_stopped"] and state["proxy_stopped"]:
            try:
                # Retain stopped containers for checkpoint proof, but detach them
                # so Docker can remove this attempt's private network.
                for container in reversed(created):
                    docker.call("network", "disconnect", "--force", network, container)
                docker.call("network", "rm", network)
                state["internal_network_removed"] = True
            except (RunError, OSError, subprocess.SubprocessError):
                state["internal_network_removed"] = False
                state["status"] = "cleanup_failed"
                returncode = 1
        private_json(attempt / "run-state.json", state)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    return state, returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Existing absolute private directory; inputs are ROOT/inputs")
    parser.add_argument("--image", required=True, help="Explicit locally available image tag or sha256 digest")
    parser.add_argument("--task", required=True, help="Absolute authorized phase task JSON")
    parser.add_argument("--upstream-env", required=True, help="Absolute preconfigured proxy-only Docker env file (never read)")
    parser.add_argument("--upstream-ip", default="62.234.50.20",
                        help="Fixed proxy domain's authorized public IPv4; TLS still verifies the domain")
    parser.add_argument("--checkpoint", help="Absolute prior stopped attempt directory with home/project and run-state.json")
    parser.add_argument("--continue-needs-input", action="store_true",
                        help="Explicit private generate-to-generate handoff; requires trusted task.continuation hashes, never accepts/publishes a base; running still calls the paid model")
    parser.add_argument("--deadline-seconds", type=int,
                        help="Explicit sample hard limit; default planning 600/other 1800, maximum planning 600/other 3600")
    args = parser.parse_args()
    try:
        state, code = run(args)
    except Exception as exc:
        state = {"schema_version": 1, "status": "rejected", "delivery_published": False,
                 "error_code": str(exc) if isinstance(exc, RunError) else type(exc).__name__}
        code = 1
    print(json.dumps(state, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
