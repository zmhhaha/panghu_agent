# Scientific Pi Linux OS and OOM acceptance

The [deployment record](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md)
contains current production images and real-source acceptance. The isolated
mechanism checks below retain their narrower scope.

This suite accepts Linux containment mechanisms with a synthetic CLI. It does not accept a real Pi executable, model, tool protocol, research quality, registry image digest, network integration, or deployment. It never calls a model endpoint. All API-key values are explicit dummy strings.

The suite exercises production `PiRuntime` with `fixture_mode=False` and its real launcher. Test-only settings assert synthetic evidence flags and an all-zero synthetic image digest to reach the OS path. Those values must not be used as deployment evidence. The synthetic executable reports version `0.0.0-linux-os-fixture-v1` and a `linux_os_fixture` JSONL event. Synthetic assistant/agent events only let the adapter finish an OS test. Node version probes use the actual installed `node` executable and require major version >=20.

Run from `/app` in the dedicated acceptance image, containing the application, reviewed local skill resources, and the complete tests tree:

```sh
python -m unittest discover -s tests/scientific_pi/linux -p 'test_*.py' -v
```

The parent task owns image creation and execution. A suitable invocation of an already built acceptance image is:

```sh
docker run --rm --init \
  --user 0:0 \
  --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --cap-add SETUID --cap-add SETGID --cap-add KILL \
  --read-only --network none \
  --security-opt no-new-privileges=true \
  --tmpfs /data:rw,nosuid,nodev,noexec,mode=1777,size=128m \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777,size=64m \
  --workdir /app \
  --entrypoint python \
  ACCEPTANCE_IMAGE \
  -m unittest discover -s tests/scientific_pi/linux -p 'test_*.py' -v
```

Keep the default private PID namespace. Do not add host PID/network sharing, privileged mode, host sockets, service-account tokens, writable source mounts, real credentials, or extra capabilities. `CHOWN`, `DAC_OVERRIDE`, `KILL`, `SETGID`, `SETUID` are Linux capability bits 0, 1, 5, 6, 7; the exact mask is `0xe3`. The suite checks effective, permitted, and bounding controller masks and requires empty inheritable/ambient masks. `SETPCAP`, `FOWNER`, and `SYS_ADMIN` are not required.

On Windows or another non-Linux OS, all seven tests skip with a Linux requirement. On Linux, missing prerequisites fail class setup; a green run containing only skips is not acceptable. Prerequisites include root, the exact five capabilities, readonly root, writable tmpfs mounts at `/data` and `/tmp`, loopback-only networking, Python/kernel pidfd support, actual Node >=20, and readable reviewed resources. The suite rejects known host init visibility and common host-socket/service-account paths. Private PID namespace and absence of arbitrary nonstandard host mounts still require the recorded external container launch command; a process cannot independently attest every host launch property from inside its namespace.

Coverage:

- Real `setresuid`/`setresgid` to a fresh persisted test UID >=200000, empty supplementary groups, zero effective/permitted/inheritable/ambient capabilities, and `NoNewPrivs=1` observed from the executed CLI.
- Actual RLIMIT_NPROC soft/hard limits of 16, with live concurrent forks until the kernel returns `EAGAIN`. The test reaps those bounded children before continuing.
- Minimal child environment with one dummy model API-key entry; controller credential markers and loader options do not appear.
- Read/write denial for root-private controller/result files, directory enumeration/create denial, permission denial when probing a root controller PID, and isolation from the directory/files of an earlier real attempt with a different UID.
- A `setsid` descendant that ignores SIGTERM, with no inherited stdout/stderr pipes. Normal completion, running cancellation, and a running execution deadline must leave no live process with the attempt UID. Journal/config cleanup and absent failure provenance are checked where applicable.
- A sacrificial root controller writes its job UID to a root-private test file, then the production runtime writes a containment journal. The test kills that controller with SIGKILL, observes PR_SET_PDEATHSIG terminate the root task, confirms its detached descendant remains alive, and recovers the descendant through (a) the runtime's persisted UID journal and (b) the separately persisted job UID after removing that journal. The UID is not inferred from a potentially reused PID. This tests the runtime recovery API with synthetic durable job state, not scheduler/store crash consistency.
- An actual task-created FIFO named `report.md` is rejected by production `safe_read` and `validate_and_copy` before validation/publication. A bounded subprocess ensures a regression cannot hang the suite indefinitely.

UIDs are allocated monotonically from a root-owned counter in `/data`; they are not reused within a surviving acceptance tmpfs. Workspaces are child-owned mode 0700, their parent permits traversal only, and controls/results are root-owned mode 0700. Every test performs final production UID recovery even after an assertion failure. Zombies count as stopped because they cannot execute; `--init` provides normal orphan reaping.

Retain the exact image ID/digest, full container launch argv, container platform/kernel, stdout/stderr, unittest exit status, and named test results. Successful Linux acceptance means seven executed tests, zero failures/errors, zero skips, and the explicit synthetic-scope report. Record Windows discovery/compile results separately. Never label a Windows skip or this synthetic suite as real Pi/model/protocol acceptance.

Authoring-host verification: Windows discovery completed with seven expected Linux-only skips, and `python -m compileall -q tests/scientific_pi/linux` succeeded. These are separate from the Linux execution evidence below.

The parent agent executed the complete suite on 2026-10-22 and reported **all seven Linux tests passed in 7.289 seconds, with zero failures, errors, or skips**. This includes the actual controller SIGKILL, parent-death signal, and persisted UID recovery cases. This document records the parent's execution report; the test-authoring agent did not independently run or inspect the remote container log.

Parent-recorded execution details:

- Platform: Linux ARM64 on the parent's `master` host; kernel `6.1.115-vendor-rk35xx`; Docker `29.6.1`.
- Worker base image configuration ID: `sha256:23b7d9f1f89273e38bc61028cc25863655c6b446db802ab0e9e195d868ff9709`.
- Parent-recorded source SHA-256: `a86a8fc4d7fed457baa23824f9010b9d61ce93af41ea3ae5c6c40548baa4243d`.
- Dedicated OS acceptance image ID prefix: `db5efc5fd727`. This is a truncated local image identifier, not a complete registry digest.
- The parent retained the exact launch in its `.research-temp/run-scientific-pi-linux.sh`. Reported options were `docker run --rm --read-only --network none`, `--cap-drop ALL` plus exactly the five named capabilities, `--security-opt no-new-privileges=true`, `--pids-limit 256`, `--memory 1g`, `--cpus 1`, tmpfs `/data` of 256 MiB, and tmpfs `/tmp` of 128 MiB. The parent reported a private PID namespace and no host mounts, host sockets, or service-account mounts. These are the actual run parameters; the earlier command is an example and uses different tmpfs sizes.
- Suite stdout explicitly reported scope `synthetic-linux-os-only`, controller mask `0xe3`, readonly root, and only the `lo` network interface.
- Durable log on the parent's Linux host: `/root/scientific-pi-validation-20261022-1214/linux-os-test.log`.

The passing run establishes the seven synthetic Linux OS mechanism checks described above under the reported container configuration. It does not establish real Pi/model/tool-protocol acceptance, scheduler/store crash consistency, research quality, deployment readiness, or a verified registry image digest. Test-only synthetic evidence flags and image digest remain synthetic despite the successful OS run.

Follow-up observation: production `recover()` removes the runtime containment journal after successful UID cleanup but leaves root-private `launch-*.json` configuration files after a controller crash. The crash test explicitly removes these stale configurations after verifying process cleanup. Retention cleanup is a follow-up; the passing run did not identify a live-process safety blocker.

## 2026-10-22: real kernel OOM and queue/report recovery

The separate [OOM recovery probe](../tests/scientific_pi/linux/oom_recovery_probe.py) was executed on the Linux ARM64 host using the dedicated acceptance image below. It creates a fresh directory and three disposable containers: a real OOM of the synthetic worker/controller, a replacement worker using the same private SQLite queue and result directory, and a fresh-container readback. It never mounts production data, connects to the network, calls a model or writes the shared report database.

The exact successful invocation was:

```sh
python3 /root/scientific-pi-rollout-20261022/oom-test/oom_recovery_probe.py \
  --image arm-cluster-master:5000/scientific-pi-acceptance@sha256:53caf92c5591dbfad98d9003ed380d5520af99a326fc393a860ca49835f4c3a1 \
  --output-parent /root/scientific-pi-rollout-20261022/oom-test
```

To reproduce, copy the current probe to that dedicated script directory with readable source permissions (`0755` directory, `0644` script), or invoke its repository path on a Linux Docker host with an existing private output parent. Each invocation creates a new `scientific-pi-oom-*` child directory. The acceptance image must contain `/app`, its complete tests tree, actual Node, and the reviewed local skills. `--self-test` checks local argument/constants and exclusive JSON writes without Docker or network calls; it does not prove OOM behavior.

The launcher records Docker arguments in the implementation: `--network none`, `--read-only`, a private PID namespace, exactly CHOWN/DAC_OVERRIDE/KILL/SETGID/SETUID, `no-new-privileges`, `--memory 268435456 --memory-swap 268435456`, `--pids-limit 64`, one CPU, a 16 MiB `/tmp`, a read-only script directory at `/probe`, and only the newly created test directory at `/data`. The synthetic worker receives dummy credentials only. A lightweight PID1 supervisor retains the cgroup OOM counter while the actual worker/controller touches memory pages until the kernel kills it. The supervisor exits 137 after recording that observation; no SIGKILL command is used to manufacture the crash.

Observed results:

- The cgroup `oom_kill` counter increased by **1**, and the actual worker/controller returned **-9**. The container exited **137**. This is kernel OOM evidence rather than an inference from exit 137 alone.
- **Docker `State.OOMKilled` was false**, including after a delayed recheck of the initial direct-PID1 attempt. The successful supervised run also reports `docker_oom_flag_verified=false`. Docker's OOM metadata was not verified and is not presented as passing evidence.
- The replacement worker found the persisted running attempt and UID journal. Its actual scheduler called runtime recovery before requeueing, rejected the old lease as stale, and allocated UID **200001** after **200000**. The task completed on attempt **2**, with `max_attempts=2`; no third execution was observed. This run covers one bounded recovery, not repeated-OOM exhaustion of the retry budget.
- Recovery to the publishing state and local acknowledgement took **0.712 seconds**. Validation passed, all **8** artifacts matched their sizes and SHA256 digests, and both attempt UIDs had **zero live processes**.
- A third fresh container reopened the same queue and read the succeeded task, report ID, UID allocation history, result digest and unchanged report. Report SHA256: `fbd251f9bdeb3d362bef3d81e1e5fc9a695190bd0b2e329d7988a4b85a58277e`.

The [five sanitized evidence records](../../docs/scientific-research-pi/evidence/20261022-rollout/oom-recovery.json) contain the aggregate result, raw cgroup counter observation, and Docker state for all three successful-run containers. Full dedicated logs and private synthetic queue/results remain at `/root/scientific-pi-rollout-20261022/oom-test/scientific-pi-oom-sj4mjkl7`. The initial direct-PID1 attempt at `scientific-pi-oom-wa4qmx77` did not establish OOM cause and was rejected; its evidence is also retained.

After recording the results, all four exact test containers were verified stopped, matched to the pinned acceptance image and independent test mounts, and removed without deleting volumes or directories. [Cleanup evidence](../../docs/scientific-research-pi/evidence/20261022-rollout/oom-container-cleanup.json) records their IDs, stopped state and exit codes. The server copy is `/root/scientific-pi-rollout-20261022/oom-test/container-cleanup.json`; all test data and logs remain available.

This establishes real kernel OOM of the disposable worker and persistent queue/report recovery across isolated containers. The data volume was an independent host bind directory, **not a Ceph PVC**. The CLI/report were synthetic and the publication acknowledgement was local; real model quality, shared report-store publication, Ceph persistence, API recovery and production worker restart require their separate rollout evidence. Production services were untouched by this probe.
