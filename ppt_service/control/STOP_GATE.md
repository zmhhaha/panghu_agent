# Attempt stop gate

`ppt_service/control/stop_gate.py` stops and proves termination for the dedicated UIDs of one attempt. It never launches, allocates, releases or reuses a UID, and it does not change `PptProfile.production_ready`.

```python
proof = combined_stop_proof(attempt, (pi_uid, preview_uid), resource_ids=("container:abc",))
frozen = freeze_candidate(config, attempt, layout, proof)
```

## Why a separate gate

The Pi runtime sweeps its own UID before startup and after completion. A preview helper must therefore run under a different UID, and a single-UID `StopProof` cannot describe that attempt. `combined_stop_proof` stops every listed UID and returns one `StopProof` whose `uid` is still the attempt identity, with the OS UIDs appended to `resource_ids`. The existing collector already requires `proof.uid == attempt.uid` and `all_writers_stopped is True`, so no collector change was needed: the combined proof is what the collector consumes, as verified by an integration test.

Callers must pass **every** UID reserved for the attempt, including preview/helper identities, and must verify each one against the durable ledger (`PiUIDAllocator.verify`) before launch. This function proves only that the listed UIDs have no live process. Container, cgroup, network and storage resources remain the caller's responsibility and belong in `resource_ids`.

## Termination protocol

`stop_uids` scans `/proc/<pid>/status` for the real, effective or saved UID, ignoring zombie and dead states, and sends `SIGKILL` through a `pidfd` after re-checking that the pid still belongs to that UID. A UID counts as stopped only after **two consecutive empty scans**, which avoids declaring success against a process that was forked between the scan and the kill. If any listed UID still has a live process at the deadline, `stop_uids` raises `ControlError('writers_not_stopped')` and no proof is produced; callers must quarantine rather than retry or export.

Detached descendants are covered because termination is by UID rather than by process group. A process that dropped its UID therefore survives by design — this is the intended containment boundary, not an oversight.

## Requirements and refusals

Linux root controller with `CAP_KILL` (and `CAP_SETUID`/`CAP_SETGID` for tests that create the victim processes) is required; `pidfd_open`/`pidfd_send_signal` must be callable. Non-Linux hosts, non-root callers, missing pidfd support, unreadable `/proc` state, invalid UIDs (below 10000, the `nobody` UID 65534, out of range, non-integer, booleans), duplicate UIDs, a bare integer passed instead of a sequence, grace outside 0–60 seconds, and malformed resource identifiers all fail closed before any scan.

A scan error is never treated as "stopped". A missing or unreadable process state aborts the proof.

## Limits

`StopProof` remains a trusted in-process contract, **not** an authenticated certificate. Across a service boundary, a caller-supplied `all_writers_stopped` boolean is not evidence; a service must observe its own resources and return attempt-bound evidence that the caller can verify. The gate also does not prove that a UID was reserved by this ledger — that is `PiUIDAllocator.verify` — and does not protect against a malicious concurrent root writer.

## Verification

Run from `panghu_agent`:

```
python -m unittest ppt_service.tests.control.test_stop_gate -v
```

Verified 2026-10-22: Windows ran 13 cases with 11 passing and 2 Linux-only skips. The parent independently ran the stop gate together with the ledger suite on Linux as root — 34 cases, zero skips, 6.888 s — in the disposable API-image runner (network disabled, readonly root/source, no-new-privileges, only CHOWN/SETUID/SETGID/KILL, 512 MiB/1 CPU/pids 256, private tmpfs, `--rm` container removed by exact name). The Linux cases start real processes under two separate UIDs plus one bystander UID, prove both attempt UIDs are killed with `SIGKILL` exit status, prove a descendant of an attempt UID is also killed, and prove the bystander UID keeps running.

Not covered: an unkillable process, kernel-level UID reuse, power loss, container/cgroup cleanup, network policy, actual Pi execution, preview rendering, or any publication gate. This module is not wired into dispatch and does not enable production execution.
