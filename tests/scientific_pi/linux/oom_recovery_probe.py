#!/usr/bin/env python3
"""Opt-in Docker OOM/recovery acceptance using synthetic research and private data.

The host command creates three dedicated containers and a fresh directory. A
kernel cgroup oom_kill counter increase plus worker SIGKILL is required; Docker's
OOMKilled field is recorded separately and never inferred from exit 137.
No network, model keys,
production volumes, report service or mail is used. The shared bind directory
tests persistence across containers; it does not certify Ceph/PVC behavior.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

CASE = Path("/data/oom-case")
SOURCE = Path("/app")
SCRIPT = Path("/probe/oom_recovery_probe.py")
MEMORY = 256 * 1024 * 1024
CAPS = ("CHOWN", "DAC_OVERRIDE", "KILL", "SETGID", "SETUID")


class Failure(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Failure(code)


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    Path(path).chmod(0o600)


def wait_for(predicate, code, seconds=45):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        found = predicate()
        if found:
            return found
        time.sleep(0.05)
    raise Failure(code)


def memory_limit():
    for path in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        if path.exists():
            raw = path.read_text().strip()
            require(raw.isdigit() and 64 * 1024 * 1024 <= int(raw) <= 512 * 1024 * 1024, "bounded_cgroup_memory_required")
            return int(raw)
    raise Failure("memory_cgroup_unavailable")


def prerequisites():
    require(sys.platform == "linux" and os.geteuid() == 0 and (os.getpid() == 1 or os.getppid() == 1), "private_container_controller_required")
    require({name for _, name in socket.if_nameindex()} == {"lo"}, "network_none_required")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    mask = sum(1 << bit for bit in (0, 1, 5, 6, 7))
    require(all(int(status[field].strip(), 16) == mask for field in ("CapEff", "CapPrm", "CapBnd")), "capability_mask_mismatch")
    require(not any("SCIENTIFIC_PI_MODEL_API_KEY" == key or "SCIENTIFIC_PI_WORKER_TOKEN" == key for key in os.environ), "real_credentials_not_allowed")
    mounts = {line.split(" - ", 1)[0].split()[4]: line.split(" - ", 1)[0].split()[5].split(",") for line in Path("/proc/self/mountinfo").read_text().splitlines()}
    require("ro" in mounts.get("/", []) and "rw" in mounts.get("/data", []) and "ro" in mounts.get("/probe", []), "mount_layout_invalid")
    require(not any(Path(path).exists() for path in ("/var/run/docker.sock", "/run/containerd/containerd.sock", "/var/run/secrets/kubernetes.io/serviceaccount")), "forbidden_host_mount")
    return memory_limit()


def oom_kills():
    for path in (Path("/sys/fs/cgroup/memory.events"), Path("/sys/fs/cgroup/memory/memory.oom_control")):
        if path.exists():
            values = dict(line.split() for line in path.read_text().splitlines())
            if "oom_kill" in values:
                return int(values["oom_kill"])
    raise Failure("cgroup_oom_counter_missing")


def supervise_crash():
    require(os.getpid() == 1, "container_supervisor_pid1_required")
    prerequisites()
    before = oom_kills()
    process = subprocess.run([sys.executable, str(SCRIPT), "--phase", "crash-worker"], timeout=100)
    after = oom_kills()
    evidence = dict(worker_returncode=process.returncode, cgroup_oom_kill_before=before,
                    cgroup_oom_kill_after=after, worker_killed_by_real_oom=process.returncode == -9 and after > before)
    save(Path("/data/worker-oom.json"), evidence)
    require(evidence["worker_killed_by_real_oom"], "worker_oom_not_confirmed")
    # Keep PID1 alive briefly so Docker can consume the real cgroup OOM event.
    # The actual worker/controller was killed by the kernel, never by this test.
    time.sleep(1)
    os._exit(137)


def synthetic_cli():
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(SOURCE / "tests/scientific_pi/research"))
    if "--version" in sys.argv:
        print("0.0.0-linux-os-fixture-v1")
        return
    from fixtures import abstract_only, write_fixture
    write_fixture(Path.cwd(), abstract_only())
    provider = sys.argv[sys.argv.index("--provider") + 1]
    model = sys.argv[sys.argv.index("--model") + 1]
    message = dict(role="assistant", provider=provider, model=model, stopReason="stop", content=[])
    for event in ({"type": "agent_start"}, {"type": "message_end", "message": message}, {"type": "agent_end", "messages": [message]}):
        print(json.dumps(event), flush=True)


def controller(phase):
    if phase == "crash":
        return supervise_crash()
    if phase == "crash-worker":
        phase = "crash"
    limit = prerequisites()
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(SOURCE / "tests/scientific_pi/linux"))
    from acceptance_support import settings, make_job, write_private
    from pi_worker.artifacts import artifact_bytes
    from pi_worker.runtime import PiRuntime
    from pi_worker.scheduler import InstanceLock, Scheduler
    from pi_worker.store import QueueStore
    from scientific_agent.pi_contracts import ContractError, report_sha256, result_digest

    if phase == "crash":
        require(not CASE.exists(), "fresh_data_directory_required")
        CASE.mkdir(mode=0o711)
        for name in ("control", "results"):
            (CASE / name).mkdir(mode=0o700)
            write_private(CASE / name / "private.json", {"synthetic_sentinel": name})
        config = CASE / "fixture.json"
        config.write_text(json.dumps(dict(mode="crash", max_processes=16, root_controller_pid=os.getpid(),
                                         denied_directories=[str(CASE / "control"), str(CASE / "results")],
                                         denied_paths=[str(CASE / "control/private.json"), str(CASE / "results/private.json")])))
        config.chmod(0o644)
    else:
        attestation = json.loads(Path("/data/oom-attestation.json").read_text())
        require(attestation.get("worker_killed_by_real_oom") is True and attestation.get("exit_code") == 137 and attestation.get("memory_bytes") == MEMORY, "host_oom_attestation_required")
        require(CASE.is_dir() and not CASE.is_symlink(), "persisted_data_missing")
        config = CASE / "fixture.json"

    lock = InstanceLock(CASE / "control/instance.lock").acquire()
    store = QueueStore(CASE / "control/queue.sqlite")
    runtime_settings = settings(CASE, config)
    if phase != "crash":
        runtime_settings = replace(runtime_settings, command=(sys.executable, str(SCRIPT), "--synthetic-cli"))

    class ObservedRuntime(PiRuntime):
        recovered = False
        recovered_uids = ()
        def recover(self, uids=()):
            self.recovered_uids = tuple(uids)
            result = super().recover(uids)
            self.recovered = result is True
            return result

    runtime = ObservedRuntime(runtime_settings)
    scheduler = None
    try:
        if phase == "crash":
            job = make_job(200000, seconds=900)
            job.pop("_runtime_uid")
            job["timeout_seconds"] = 900
            from scientific_agent.pi_contracts import input_digest
            job["input_digest"] = input_digest(job)
            snapshot, created = store.submit(job)
            require(created, "fresh_job_required")
            task_id = snapshot["id"]
            require(runtime.health()["ready"] is True, "synthetic_runtime_not_ready")
            scheduler = Scheduler(store, runtime, CASE, max_attempts=2)
            scheduler.start()
            workspace = CASE / "tasks" / task_id / "attempts/1/workspace"
            wait_for(lambda: (workspace / "observed.json").exists(), "first_attempt_not_running")
            observation = json.loads((workspace / "observed.json").read_text())
            with store._tx() as db:
                record = dict(db.execute("SELECT lease FROM tasks WHERE id=?", (task_id,)).fetchone())
            uids = store.execution_uids()
            require(store.get(task_id)["state"] == "running" and len(uids) == 1 and observation["uid"] == [uids[0]] * 3, "first_attempt_identity_unverified")
            require(list((CASE / "control").glob("runtime-*.json")), "runtime_journal_missing")
            require(PiRuntime._uid_processes(uids[0]), "first_uid_process_missing")
            save(CASE / "control/crash-before.json", dict(task_id=task_id, old_lease=record["lease"], uid=uids[0], attempt=1,
                                                         state="running", journal_present=True, child_live=True, memory_limit=limit))
            print(json.dumps(dict(stage="oom_armed", task_id=task_id, attempt=1, uid=uids[0], memory_bytes=limit)), flush=True)
            # Kill eligibility is raised only for this disposable worker process.
            # Touch each memory page so the kernel must charge real memory.
            Path("/proc/self/oom_score_adj").write_text("1000")
            allocated = []
            for _ in range((limit * 2) // (8 * 1024 * 1024)):
                block = bytearray(8 * 1024 * 1024)
                for offset in range(0, len(block), 4096):
                    block[offset] = 1
                allocated.append(block)
                time.sleep(0.01)
            raise Failure("real_oom_did_not_occur")

        before = json.loads((CASE / "control/crash-before.json").read_text())
        task_id = before["task_id"]
        if phase == "recover":
            require(store.get(task_id)["state"] == "running" and store.get(task_id)["attempt"] == 1, "queue_did_not_survive_oom")
            require(store.execution_uids() == [before["uid"]], "persisted_uid_missing")
            require(list((CASE / "control").glob("runtime-*.json")), "persisted_journal_missing")
            require(not PiRuntime._uid_processes(before["uid"]), "old_container_uid_still_live")
            scheduler = Scheduler(store, runtime, CASE, max_attempts=2)
            started = time.monotonic()
            scheduler.start()
            require(runtime.recovered and runtime.recovered_uids == (before["uid"],), "runtime_recovery_not_called")
            try:
                store.renew(task_id, 1, before["old_lease"])
            except ContractError as exc:
                require(exc.code == "stale_attempt", "unexpected_fence_error")
            else:
                raise Failure("old_lease_not_fenced")
            def finished():
                current = store.get(task_id)
                if scheduler.degraded or current["state"] in {"failed", "timed_out", "cancelled"}:
                    raise Failure("recovered_attempt_failed")
                return current if current["state"] == "publishing" else None
            snapshot = wait_for(finished, "recovery_publish_timeout")
            require(snapshot["attempt"] == 2, "recovery_attempt_not_bounded")
            uids = store.execution_uids()
            require(len(uids) == 2 and uids[0] == before["uid"] and uids[1] > uids[0], "uid_reused")
            require(all(not PiRuntime._uid_processes(uid) for uid in uids), "uid_cleanup_unverified")
            result = store.result(task_id)
            require(result["validation"]["valid"] is True and report_sha256(result["report_markdown"]) == result["report_sha256"], "report_validation_failed")
            require(result_digest(result) == result["result_digest"], "result_digest_mismatch")
            for entry in result["artifacts"]:
                content, _ = artifact_bytes(store, task_id, entry["artifact_id"])
                require(len(content) == entry["size"] and hashlib.sha256(content).hexdigest() == entry["sha256"], "artifact_integrity_failed")
            # Explicit local publication acknowledgement, never a production API
            # report-store write or evidence of external publication durability.
            store.ack(task_id, 2, result["result_digest"], task_id)
            require(store.claim() is None and store.get(task_id)["state"] == "succeeded", "unexpected_extra_attempt")
            evidence = dict(valid=True, stage="recovered", task_id=task_id, old_attempt_fenced=True, queue_survived_oom=True,
                            runtime_recovery_called=True, uid_sequence=uids, uid_reused=False, live_uid_processes=0,
                            attempts=2, configured_max_attempts=2, bounded_requeue=True, validation_valid=True,
                            artifact_hashes_verified=len(result["artifacts"]), report_sha256=result["report_sha256"], result_digest=result["result_digest"],
                            local_publication_ack=True, recovery_seconds=round(time.monotonic() - started, 3),
                            synthetic_cli=True, real_model=False, shared_report_store_verified=False, ceph_pvc_verified=False)
            save(Path("/data/recovery-result.json"), evidence)
            return evidence

        require(phase == "check", "unknown_controller_phase")
        accepted = json.loads(Path("/data/recovery-result.json").read_text())
        snapshot = store.get(task_id)
        result = store.result(task_id)
        data, _ = artifact_bytes(store, task_id, "report")
        require(snapshot["state"] == "succeeded" and snapshot["attempt"] == 2 and snapshot["report_id"] == task_id, "published_state_not_persistent")
        require(hashlib.sha256(data).hexdigest() == accepted["report_sha256"] == result["report_sha256"], "persistent_report_digest_mismatch")
        require(store.execution_uids() == accepted["uid_sequence"] and result_digest(result) == accepted["result_digest"], "persistent_metadata_mismatch")
        return dict(valid=True, stage="readback", fresh_container_readback=True, report_preserved=True, task_id=task_id,
                    report_sha256=result["report_sha256"], ceph_pvc_verified=False)
    finally:
        if scheduler is None or scheduler.stop():
            lock.release()


def docker(arguments, timeout=45):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, "docker_command_failed")
    return result.stdout.strip()


def host_run(image, parent):
    require(sys.platform == "linux", "linux_docker_host_required")
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}", image), "pinned_acceptance_image_required")
    parent = Path(parent).resolve(strict=True)
    require(parent.is_dir(), "output_parent_missing")
    directory = Path(tempfile.mkdtemp(prefix="scientific-pi-oom-", dir=parent))
    directory.chmod(0o755)  # Container's isolated task UIDs must traverse /data.
    source = Path(__file__).resolve()
    require("," not in str(source.parent) + str(directory), "mount_path_invalid")
    containers = []
    try:
        for phase in ("crash", "recover", "check"):
            args = ["run", "--detach", "--network", "none", "--read-only", "--memory", str(MEMORY), "--memory-swap", str(MEMORY),
                    "--pids-limit", "64", "--cpus", "1", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", "--mount", f"type=bind,src={directory},dst=/data",
                    "--mount", f"type=bind,src={source.parent},dst=/probe,readonly", "--entrypoint", "python"]
            for capability in CAPS:
                args += ["--cap-add", capability]
            args += [image, str(SCRIPT), "--phase", phase]
            container = docker(args)
            require(re.fullmatch(r"[0-9a-f]{64}", container), "container_id_invalid")
            containers.append(container)
            try:
                docker(["wait", container], timeout=120)
            except subprocess.TimeoutExpired:
                docker(["stop", "--time", "3", container], timeout=15)
                raise Failure("container_stage_timeout")
            info = json.loads(docker(["inspect", container]))[0]
            state = info["State"]
            record = dict(container_id=container, stage=phase, oom_killed=state.get("OOMKilled") is True,
                          exit_code=state["ExitCode"], memory_bytes=info["HostConfig"]["Memory"],
                          network_mode=info["HostConfig"]["NetworkMode"], readonly_rootfs=info["HostConfig"]["ReadonlyRootfs"])
            save(directory / (phase + "-container.json"), record)
            # Dedicated fixtures print only state/IDs or sanitized failure codes.
            (directory / (phase + ".log")).write_text(docker(["logs", container]), encoding="utf-8")
            if phase == "crash":
                require(record["exit_code"] == 137, "container_oom_exit_missing")
                worker_oom = json.loads((directory / "worker-oom.json").read_text())
                require(worker_oom.get("worker_killed_by_real_oom") is True, "worker_oom_evidence_missing")
                require((directory / "oom-case/control/crash-before.json").is_file(), "oom_precondition_evidence_missing")
                oom_record = dict(record, worker_killed_by_real_oom=True)
                save(directory / "oom-attestation.json", oom_record)
            else:
                require(not record["oom_killed"] and record["exit_code"] == 0, "recovery_container_failed")
        recovery = json.loads((directory / "recovery-result.json").read_text())
        readback = json.loads((directory / "check.log").read_text())
        require(recovery.get("valid") is True and readback.get("report_preserved") is True, "readback_failed")
        result = dict(recovery, code="docker_oom_recovery_verified" if oom_record["oom_killed"] else "cgroup_oom_recovery_verified",
                      docker_oom_killed=oom_record["oom_killed"], docker_oom_flag_verified=oom_record["oom_killed"], oom_exit_code=137,
                      worker_killed_by_real_oom=True, cgroup_oom_kill_delta=worker_oom["cgroup_oom_kill_after"] - worker_oom["cgroup_oom_kill_before"],
                      fresh_container_readback=True, report_preserved=True, evidence_directory=str(directory),
                      stopped_container_ids=containers, production_touched=False)
        save(directory / "result.json", result)
        return result
    except BaseException:
        # Preserve the private test directory and stopped containers for review.
        for container in containers:
            info = json.loads(docker(["inspect", container]))[0]
            if info["State"]["Running"]:
                docker(["stop", "--time", "3", container], timeout=15)
        print(json.dumps(dict(valid=False, evidence_directory=str(directory), stopped_container_ids=containers)), file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--output-parent", default="/tmp")
    parser.add_argument("--phase", choices=("crash", "crash-worker", "recover", "check"), help=argparse.SUPPRESS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            require(MEMORY == 256 * 1024 * 1024 and CASE.as_posix() == "/data/oom-case" and len(CAPS) == 5, "constant_self_test_failed")
            with tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / "record.json"
                save(target, {"synthetic": True})
                require(json.loads(target.read_text()) == {"synthetic": True}, "durable_json_self_test_failed")
                try:
                    save(target, {})
                except FileExistsError:
                    pass
                else:
                    raise Failure("exclusive_write_self_test_failed")
            result = dict(valid=True, code="self_test_passed", docker_calls=0, network_requests=0, actual_oom_tested=False)
        elif args.phase:
            result = controller(args.phase)
        else:
            result = host_run(args.image or "", args.output_parent)
    except Exception as exc:
        result = dict(valid=False, code=str(exc) if isinstance(exc, Failure) else "oom_probe_failed", exception_type=type(exc).__name__, production_touched=False)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    if "--synthetic-cli" in sys.argv:
        synthetic_cli()
    else:
        raise SystemExit(main())
