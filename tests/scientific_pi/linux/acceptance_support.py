"""Setup shared by the unittest controller and its sacrificial crash controller.

All evidence flags below are synthetic and local to these OS mechanism tests.
They must never be copied into deployment configuration or model acceptance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pi_worker.runtime import PiRuntime, RuntimeSettings
from scientific_agent.pi_contracts import input_digest
from pi_worker.research_tools import BUNDLE_REVISION

FIXTURE = Path(__file__).with_name("synthetic_cli.py")
FIXTURE_VERSION = "0.0.0-linux-os-fixture-v1"
MAX_PROCESSES = 16
CAPABILITIES = {"CHOWN": 0, "DAC_OVERRIDE": 1, "KILL": 5, "SETGID": 6, "SETUID": 7}
CAPABILITY_MASK = sum(1 << number for number in CAPABILITIES.values())


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())


def make_job(uid, seconds=45):
    now = datetime.now(timezone.utc)
    job = {"schema_version": "1", "task_id": str(uuid.uuid4()), "topic": "Synthetic Linux OS mechanism acceptance",
           "user_id": "synthetic-linux-user", "engine": "pi", "mode": "review", "timeout_seconds": 60,
           "created_at": now.isoformat(), "execution_deadline_at": (now + timedelta(seconds=seconds)).isoformat(),
           "skill_bundle_revision": BUNDLE_REVISION, "runtime_revision": "synthetic-linux-os-v1"}
    job["input_digest"] = input_digest(job)
    job["_runtime_uid"] = uid
    return job


def settings(case, config):
    skill = ROOT / "scientific_skills" / "research-procedure" / "SKILL.md"
    manifest = ROOT / "scientific_skills" / "manifest.json"
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Linux acceptance requires the actual node executable >=20; no synthetic Node version")
    return RuntimeSettings(
        command=(sys.executable, str(FIXTURE), "--case-config", str(config)), node_command=(node,),
        provider="synthetic-os-only", model="synthetic-no-model", expected_pi_version=FIXTURE_VERSION,
        skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
        bundle_manifest=manifest, bundle_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        skill_bundle_revision=BUNDLE_REVISION, runtime_revision="synthetic-linux-os-v1",
        # Explicit synthetic bypass of evidence gates, never evidence about Pi/model readiness.
        fixture_mode=False, capability_verified=True, model_verified=True, isolation_verified=True,
        image_digest="sha256:" + "0" * 64, model_env={"SYNTHETIC_API_KEY": "dummy-not-a-secret"},
        control_dir=case / "control", denied_paths=(case / "control" / "private.json", case / "results" / "private.json"),
        run_uid_start=200000, run_gid=200000, max_processes=MAX_PROCESSES,
        term_grace_seconds=0.1, kill_grace_seconds=3, idle_timeout_seconds=30)


def crash_controller(case, config, persisted):
    case, config, persisted = map(Path, (case, config, persisted))
    job = json.loads(persisted.read_text())
    runtime = PiRuntime(settings(case, config))
    # The test parent owns durable job/UID state. Here the actual runtime writes
    # its own containment journal before launching the child.
    runtime.execute(job, case / "crash-attempt", lambda event: None, threading.Event())


if __name__ == "__main__":
    crash_controller(*sys.argv[1:])
