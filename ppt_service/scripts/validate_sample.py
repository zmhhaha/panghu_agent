"""Read-only candidate integrity checks on a stopped local Docker sample attempt.

Run on the trusted Linux Docker host. This does not publish, confirm a plan,
execute quality reports, or establish visual/factual/PowerPoint acceptance.
The controller must prevent container restarts and other writers throughout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ppt_service.worker.candidate_validation import validate_manifest


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def checked_path(raw, *, directory=False):
    path = Path(raw)
    require(path.is_absolute() and ".." not in path.parts, "absolute_no_traversal_path_required")
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "symlink_or_reparse_path_rejected")
        if item != path or directory:
            require(stat.S_ISDIR(info.st_mode), "directory_required")
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "ordinary_unlinked_file_required")
    return path


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def constant(value):
    raise Rejected("invalid_json_constant")


def document(raw, *, object_required=True, limit=1_048_576):
    path = checked_path(raw)
    require(path.stat().st_size <= limit, "json_document_too_large")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "json_document_too_large")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    require(not object_required or isinstance(value, dict), "json_object_required")
    return value


def stopped(attempt, proof, role):
    name = proof.get(role + "_container")
    require(isinstance(name, str) and re.fullmatch(r"ppt-sample-[0-9a-f]{32}-" + role, name),
            "container_identity_missing")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(attempt / "docker-config"), "DOCKER_CONFIG": str(attempt / "docker-config")}
    result = subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", "container", "inspect", "--format",
         '{"state":{{json .State}},"mounts":{{json .Mounts}}}', name],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 1_048_576, "docker_inspect_failed")
    info = json.loads(result.stdout, object_pairs_hook=pairs, parse_constant=constant)
    require(info["state"].get("Running") is False and info["state"].get("Status") == "exited",
            "container_not_exited")
    if role == "worker":
        require(info["state"].get("ExitCode") == 0, "worker_exit_failed")
        for part in ("project", "result", "home"):
            require(any(m.get("Type") == "bind" and m.get("Source") == str(attempt / part)
                        and m.get("Destination") == "/work/" + part for m in info["mounts"]),
                    "worker_mount_identity_mismatch")


def inputs_verified(raw, task):
    root = checked_path(raw, directory=True)
    assets = task.get("assets")
    require(isinstance(assets, list) and 0 < len(assets) <= 2048, "invalid_trusted_assets")
    seen = set()
    for asset in assets:
        require(isinstance(asset, dict), "invalid_trusted_asset")
        name = asset.get("path")
        require(isinstance(name, str) and name.startswith("/work/inputs/"), "invalid_asset_path")
        relative = name[len("/work/inputs/"):]
        require(relative and "\\" not in relative and ":" not in relative
                and all(p not in {"", ".", ".."} for p in relative.split("/")), "invalid_asset_path")
        require(relative not in seen, "duplicate_asset_path")
        seen.add(relative)
        path = checked_path(root.joinpath(*PurePosixPath(relative).parts))
        size = asset.get("bytes")
        require(type(size) is int and 0 <= size <= 64 * 1024 * 1024
                and path.stat().st_size == size, "asset_size_mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            remaining = size
            while remaining:
                chunk = stream.read(min(1_048_576, remaining))
                require(bool(chunk), "asset_changed")
                digest.update(chunk)
                remaining -= len(chunk)
            require(not stream.read(1), "asset_changed")
        require(digest.hexdigest() == asset.get("sha256"), "asset_hash_mismatch")
    snapshot = hashlib.sha256(json.dumps(assets, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(snapshot == task.get("input_snapshot_sha256"), "input_snapshot_mismatch")


def validate(args):
    attempt = checked_path(args.attempt, directory=True)
    task = document(args.task)
    proof = document(attempt / "run-state.json")
    require(proof.get("worker_stopped") is True and proof.get("proxy_stopped") is True
            and proof.get("status") == "candidate_execution_finished", "stopped_successful_attempt_required")
    for key in ("job_id", "attempt", "phase", "input_snapshot_sha256"):
        require(key in task and proof.get(key) == task[key], "run_state_task_mismatch")
    stopped(attempt, proof, "worker")
    stopped(attempt, proof, "proxy")
    execution = document(attempt / "result" / "execution.json")
    require(execution.get("status") == "completed" and execution.get("gateway_stopped") is True,
            "completed_gateway_required")
    for key in ("phase", "session_id", "job_id", "attempt"):
        require(key in task and execution.get(key) == task[key], "execution_task_mismatch")
    manifest = document(attempt / "result" / "manifest.json", limit=4 * 1024 * 1024)
    for key in ("phase", "input_snapshot_sha256"):
        require(manifest.get(key) == task[key], "manifest_task_mismatch")
    expected = document(args.expected_charts, object_required=False) if args.expected_charts else None
    require(expected is None or isinstance(expected, list), "expected_charts_list_required")
    if args.inputs:
        inputs_verified(args.inputs, task)
    result = validate_manifest(attempt / "project", manifest, expected_charts=expected)
    if task["phase"] in {"generate", "revise"}:
        pages = task.get("requirements", {}).get("pages", 8)
        require(type(pages) is int and 3 <= pages <= 20, "invalid_trusted_page_count")
        if manifest["status"] == "candidate":
            require(len(manifest["slides"]) == pages, "trusted_page_count_mismatch")
    # Recheck the daemon after reading candidates; external locking is still required.
    stopped(attempt, proof, "worker")
    stopped(attempt, proof, "proxy")
    return {"schema_version": 1, "integrity": result, "stage": task["phase"],
            "plan": manifest.get("plan"), "questions": manifest.get("questions", []),
            "input_assets_verified": bool(args.inputs),
            "trusted_chart_oracle_supplied": expected is not None,
            "quality_reports_verified": False, "human_confirmation_recorded": False,
            "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True, help="Absolute stopped attempt directory")
    parser.add_argument("--task", required=True, help="Absolute controller-owned trusted task JSON")
    parser.add_argument("--expected-charts", help="Absolute trusted chart oracle JSON; never the candidate manifest")
    parser.add_argument("--inputs", help="Absolute authorized inputs directory; verifies trusted task assets")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args), ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "rejected", "error_code": str(exc) if isinstance(exc, Rejected)
                          else type(exc).__name__, "published": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
