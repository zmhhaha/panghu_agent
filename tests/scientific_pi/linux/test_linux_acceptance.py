"""Linux container acceptance of OS containment, using a synthetic CLI only.

Run from /app: python -m unittest discover -s tests/scientific_pi/linux -p 'test_*.py' -v
Windows/non-Linux skips this class. Missing Linux prerequisites are failures.
"""
import dataclasses
import errno
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pi_worker.artifacts import safe_read, validate_and_copy
from pi_worker.runtime import PiRuntime, RuntimeExecutionError
from scientific_agent.pi_contracts import ContractError
from acceptance_support import (CAPABILITIES, CAPABILITY_MASK, FIXTURE_VERSION, MAX_PROCESSES,
                                make_job, settings, write_private)


def proc_status(pid):
    try:
        return dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
    except FileNotFoundError:
        return None


def wait_for(predicate, description, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {description}")


@unittest.skipUnless(sys.platform == "linux", "Requires a real Linux root container; Windows cannot accept UID/capability isolation")
class LinuxContainerAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Assertions here deliberately fail Linux runs with insufficient setup.
        if os.geteuid() != 0:
            raise AssertionError("Linux acceptance requires a root controller with exactly " + ",".join(CAPABILITIES))
        status = proc_status(os.getpid())
        for field in ("CapEff", "CapPrm", "CapBnd"):
            actual = int(status[field].strip(), 16)
            if actual != CAPABILITY_MASK:
                raise AssertionError(f"{field}={actual:#x}; expected exact five-capability mask {CAPABILITY_MASK:#x}")
        for field in ("CapInh", "CapAmb"):
            if int(status[field].strip(), 16):
                raise AssertionError(f"Controller {field} must be empty")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise AssertionError("Linux acceptance requires Python/kernel pidfd support")
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
        import resource
        _, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        if hard != resource.RLIM_INFINITY and hard < MAX_PROCESSES:
            raise AssertionError("Controller inherited RLIMIT_NPROC is below test budget")
        mounts = {}
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            fields = left.split()
            mounts[fields[4]] = (fields[5].split(","), right.split()[0])
        if "ro" not in mounts["/"][0]:
            raise AssertionError("Acceptance requires --read-only root filesystem")
        for directory in ("/data", "/tmp"):
            if directory not in mounts or mounts[directory][1] != "tmpfs" or "rw" not in mounts[directory][0]:
                raise AssertionError(f"Acceptance requires a writable tmpfs at {directory}")
        if {name for _, name in socket.if_nameindex()} != {"lo"}:
            raise AssertionError("Acceptance requires --network none (loopback only)")
        for forbidden in ("/var/run/docker.sock", "/run/docker.sock", "/run/containerd/containerd.sock",
                          "/var/run/secrets/kubernetes.io/serviceaccount", "/run/secrets/kubernetes.io/serviceaccount"):
            if Path(forbidden).exists():
                raise AssertionError(f"Forbidden host socket/service-account mount: {forbidden}")
        # Host PID namespace cannot be independently attested from inside every
        # container. Reject known host init; retain external launch argv as evidence.
        if Path("/proc/1/comm").read_text().strip() in ("systemd", "init"):
            raise AssertionError("Use a private container PID namespace; host init is visible")
        if not shutil.which("node"):
            raise AssertionError("Actual node >=20 is required; synthetic Node probe is forbidden")
        print(json.dumps({"acceptance_scope": "synthetic-linux-os-only", "fixture_version": FIXTURE_VERSION,
                          "controller_capabilities": list(CAPABILITIES), "capability_mask": hex(CAPABILITY_MASK),
                          "root_readonly": True, "tmpfs": ["/data", "/tmp"], "network_interfaces": ["lo"]}), flush=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scientific-pi-linux-", dir="/data")
        self.case = Path(self.temporary.name)
        # Children must traverse the parent but cannot enumerate/write it.
        self.case.chmod(0o711)
        self.uids = []
        for name in ("control", "results"):
            directory = self.case / name
            directory.mkdir(mode=0o700)
            write_private(directory / "private.json", {"synthetic_sentinel": name})
        self.events = []

    def tearDown(self):
        try:
            # Test cleanup remains bounded even when an acceptance assertion fails.
            for uid in self.uids:
                PiRuntime(self.runtime_settings).recover((uid,))
        finally:
            self.temporary.cleanup()

    def next_uid(self):
        import fcntl
        counter = Path("/data/.scientific-pi-linux-uid-sequence")
        descriptor = os.open(counter, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            current = handle.read().strip()
            uid = int(current) + 1 if current else 200000
            handle.seek(0)
            handle.truncate()
            handle.write(str(uid))
            handle.flush()
            os.fsync(handle.fileno())
        self.assertGreaterEqual(uid, 200000)
        self.uids.append(uid)
        return uid

    def prepare(self, mode="plain", denied=(), seconds=45):
        uid = self.next_uid()
        config = self.case / f"fixture-{uid}.json"
        config.write_text(json.dumps({"mode": mode, "max_processes": MAX_PROCESSES,
            "root_controller_pid": os.getpid(),
            "denied_directories": [str(self.case / "control"), str(self.case / "results"),
                                   *[str(Path(path).parent) for path in denied]],
            "denied_paths": [str(self.case / "control" / "private.json"),
                             str(self.case / "results" / "private.json"), *map(str, denied)]}))
        config.chmod(0o644)
        self.runtime_settings = settings(self.case, config)
        runtime = PiRuntime(self.runtime_settings)
        health = runtime.health()
        self.assertTrue(health["ready"], health)
        self.assertFalse(health["fixture_mode"])
        self.assertEqual(health["pi_version"], FIXTURE_VERSION)
        self.assertGreaterEqual(int(health["node_version"].split(".")[0]), 20)
        job = make_job(uid, seconds)
        return runtime, job, self.case / f"attempt-{uid}", config

    def execute(self, runtime, job, workspace, cancel=None, sink=None):
        # Values are deliberately dummy markers, never real controller secrets.
        with patch.dict(os.environ, {"WORKER_TOKEN": "dummy-controller", "VAULT_TOKEN": "dummy-vault",
                                     "NODE_OPTIONS": "", "OPENAI_API_KEY": "dummy-unapproved-key"}):
            runtime.execute(job, workspace, sink or self.events.append, cancel or threading.Event())

    def observation(self, workspace, uid):
        observed = json.loads((workspace / "observed.json").read_text())
        self.assertEqual(observed["fixture_version"], FIXTURE_VERSION)
        self.assertEqual(observed["fixture_scope"], "linux-os-mechanisms-only")
        self.assertEqual(observed["uid"], [uid] * 3)
        self.assertEqual(observed["gid"], [uid] * 3)
        self.assertEqual(observed["groups"], [])
        for key in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
            self.assertEqual(int(observed["status"][key], 16), 0, key)
        self.assertEqual(observed["status"]["NoNewPrivs"], "1")
        self.assertEqual(observed["nproc"], [MAX_PROCESSES] * 2)
        self.assertEqual(observed["fork_errno"], errno.EAGAIN, "fork must reach actual kernel RLIMIT_NPROC refusal")
        self.assertGreater(observed["forked"], 0)
        self.assertLess(observed["forked"], MAX_PROCESSES)
        for denied in observed["denials"]:
            self.assertEqual(denied["read"], errno.EACCES, denied)
            self.assertEqual(denied["write"], errno.EACCES, denied)
        for denied in observed["directory_denials"]:
            self.assertEqual(denied["list"], errno.EACCES, denied)
            self.assertEqual(denied["create"], errno.EACCES, denied)
        self.assertEqual(observed["controller_signal_errno"], errno.EPERM)
        allowed = {"PATH", "HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR", "TMP", "TEMP",
                   "PI_CODING_AGENT_DIR", "PI_OFFLINE", "NO_COLOR", "LANG", "SYNTHETIC_API_KEY"}
        self.assertEqual(set(observed["env"]), allowed)
        self.assertEqual(observed["env"]["SYNTHETIC_API_KEY"], "dummy-not-a-secret")
        self.assertEqual(observed["env"]["HOME"], str(workspace / ".home"))
        self.assertEqual(observed["env"]["TMPDIR"], str(workspace / ".tmp"))
        return observed

    def assert_uid_stopped(self, uid):
        self.assertEqual(PiRuntime._uid_processes(uid), [], "a live process still owns the attempt UID")

    def assert_detached(self, workspace, observation):
        detached = json.loads((workspace / "detached-ready.json").read_text())
        self.assertEqual(detached["pid"], observation["detached_pid"])
        self.assertEqual(detached["sid"], detached["pid"], "descendant must have escaped original process group via setsid")
        self.assertNotEqual(detached["sid"], observation["pid"])

    def assert_clean_control(self):
        self.assertFalse(list((self.case / "control").glob("runtime-*.json")))
        self.assertFalse(list((self.case / "control").glob("launch-*.json")))

    def test_actual_uid_caps_groups_nnp_nproc_environment_and_private_controls(self):
        runtime, job, workspace, _ = self.prepare()
        self.execute(runtime, job, workspace)
        self.observation(workspace, job["_runtime_uid"])
        for name in ("control", "results"):
            stat = (self.case / name).stat()
            self.assertEqual(stat.st_uid, 0)
            self.assertEqual(stat.st_mode & 0o777, 0o700)
        self.assert_uid_stopped(job["_runtime_uid"])
        self.assert_clean_control()
        provenance = json.loads((workspace / "provenance.json").read_text())
        self.assertEqual(provenance["pi_version"], FIXTURE_VERSION)
        self.assertFalse(provenance["fixture_mode"])
        self.assertEqual(self.events[-1]["type"], "runtime_finished")

    def test_new_uid_cannot_read_or_write_previous_real_attempt(self):
        runtime, first_job, previous, _ = self.prepare()
        self.execute(runtime, first_job, previous)
        marker = previous / "previous-private.json"
        write_private(marker, {"synthetic_previous_task": True})
        os.chown(marker, first_job["_runtime_uid"], first_job["_runtime_uid"])
        runtime, next_job, workspace, _ = self.prepare(denied=(marker,))
        self.assertNotEqual(first_job["_runtime_uid"], next_job["_runtime_uid"])
        self.execute(runtime, next_job, workspace)
        self.observation(workspace, next_job["_runtime_uid"])
        self.assertEqual(json.loads(marker.read_text()), {"synthetic_previous_task": True})
        self.assert_uid_stopped(first_job["_runtime_uid"])
        self.assert_uid_stopped(next_job["_runtime_uid"])

    def test_detached_descendant_cleanup_after_normal_exit(self):
        runtime, job, workspace, _ = self.prepare("normal")
        self.execute(runtime, job, workspace)
        observed = self.observation(workspace, job["_runtime_uid"])
        self.assert_detached(workspace, observed)
        self.assert_uid_stopped(job["_runtime_uid"])
        self.assert_clean_control()

    def test_detached_descendant_cleanup_after_running_cancel(self):
        runtime, job, workspace, _ = self.prepare("cancel")
        cancel = threading.Event()
        def sink(event):
            self.events.append(event)
            if event.get("event") == "agent_start":
                cancel.set()
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(runtime, job, workspace, cancel, sink)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assert_detached(workspace, self.observation(workspace, job["_runtime_uid"]))
        self.assert_uid_stopped(job["_runtime_uid"])
        self.assertFalse((workspace / "provenance.json").exists())
        self.assert_clean_control()

    def test_detached_descendant_cleanup_after_running_deadline(self):
        runtime, job, workspace, _ = self.prepare("deadline", seconds=3)
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(runtime, job, workspace)
        self.assertEqual(caught.exception.code, "timed_out")
        self.assert_detached(workspace, self.observation(workspace, job["_runtime_uid"]))
        self.assert_uid_stopped(job["_runtime_uid"])
        self.assertFalse((workspace / "provenance.json").exists())
        self.assert_clean_control()

    def test_controller_sigkill_parentdeath_and_persisted_uid_recovery(self):
        for recovery_source in ("journal", "persisted_uid"):
            with self.subTest(recovery_source=recovery_source):
                runtime, job, _, config = self.prepare("crash")
                persisted = self.case / "control" / f"persisted-attempt-{job['_runtime_uid']}.json"
                write_private(persisted, job)
                workspace = self.case / "crash-attempt"
                # Each subcase needs a fresh empty runtime workspace.
                if workspace.exists():
                    shutil.rmtree(workspace)
                log = self.case / f"controller-{job['_runtime_uid']}.log"
                with log.open("wb") as output:
                    controller = subprocess.Popen([sys.executable, str(Path(__file__).with_name("acceptance_support.py")),
                        str(self.case), str(config), str(persisted)], cwd=Path(__file__).resolve().parents[3],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=output, close_fds=True)
                    try:
                        wait_for(lambda: (workspace / "observed.json").exists() or controller.poll() is not None,
                                 "crash fixture observation")
                        self.assertIsNone(controller.poll(), log.read_text())
                        observed = self.observation(workspace, job["_runtime_uid"])
                        self.assert_detached(workspace, observed)
                        journals = list((self.case / "control").glob("runtime-*.json"))
                        self.assertEqual(len(journals), 1)
                        record = json.loads(journals[0].read_text())
                        self.assertEqual(record["uid"], job["_runtime_uid"])
                        self.assertEqual(record["kind"], "uid")
                        controller.kill()
                        controller.wait(timeout=5)
                        def root_dead():
                            status = proc_status(observed["pid"])
                            return status is None or status["State"].strip().startswith(("Z", "X"))
                        wait_for(root_dead, "launcher PR_SET_PDEATHSIG after controller SIGKILL")
                        # Descendant deliberately has no PDEATHSIG and survives to
                        # make recovery a real kill, not merely an empty scan.
                        self.assertIn(observed["detached_pid"], PiRuntime._uid_processes(job["_runtime_uid"]))
                        recovering = PiRuntime(self.runtime_settings)
                        if recovery_source == "journal":
                            self.assertTrue(recovering.recover())
                        else:
                            journals[0].unlink()
                            durable_uid = json.loads(persisted.read_text())["_runtime_uid"]
                            self.assertTrue(recovering.recover((durable_uid,)))
                        self.assert_uid_stopped(job["_runtime_uid"])
                        self.assertFalse(list((self.case / "control").glob("runtime-*.json")))
                    finally:
                        if controller.poll() is None:
                            controller.kill()
                        controller.wait(timeout=5)
                # Recovery removes containment journals; stale root-only launcher
                # configuration is not an executable process or task-owned file.
                for launch in (self.case / "control").glob("launch-*.json"):
                    launch.unlink()

    def test_task_created_fifo_artifact_is_rejected_without_blocking(self):
        runtime, job, workspace, _ = self.prepare("fifo")
        self.execute(runtime, job, workspace)
        self.observation(workspace, job["_runtime_uid"])
        self.assert_uid_stopped(job["_runtime_uid"])
        # Isolate artifact reads in a subprocess so a blocking FIFO regression
        # fails promptly instead of hanging the entire acceptance suite.
        helper = Path(__file__).with_name("fifo_probe.py")
        process = subprocess.run([sys.executable, str(helper), str(workspace), str(self.case / "results")],
                                 cwd=Path(__file__).resolve().parents[3], stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=4, check=False)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        result = json.loads(process.stdout)
        self.assertEqual(result, {"safe_read": "invalid_artifact", "validate_and_copy": "invalid_artifact"})
        self.assertEqual(list((self.case / "results").iterdir()), [self.case / "results" / "private.json"])


if __name__ == "__main__":
    unittest.main()
