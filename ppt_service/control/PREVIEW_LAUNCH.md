# Preview launch under a dedicated UID

`ppt_service/control/preview_launch.py` starts the pinned official SVG preview server for one attempt as an unprivileged identity reserved in the UID ledger. It does not render final output, call a model, freeze artifacts or authorize publication, and it does not change `PptProfile.production_ready`.

```python
handle = launch_preview(attempt, layout, allocator, project, denied_paths=(credential_path,))
...
handle.stop(grace=10)                       # then
proof = combined_stop_proof(attempt, (pi_uid, handle.uid))   # freeze only after this
```

## Why a separate identity

The Pi runtime sweeps every process of its own UID both before startup and after completion. Starting the preview under that same UID would either kill the preview or leave a writer the Pi sweep cannot account for. The preview therefore gets its own ledger reservation (`role='preview'`), verified through `PiUIDAllocator.verify` so a caller cannot substitute an integer of its choosing.

## What the controller does

1. Requires a Linux root controller; every refusal happens before anything is spawned.
2. Resolves the preview UID from the ledger, rejecting a value that equals the Pi UID.
3. Validates the project is a marked child of the attempt (`design_spec.md` and `spec_lock.md`, ordinary single-link files), that the preview identity can traverse every ancestor from the attempt root down, and that it can write the project.
4. Creates root-owned private `preview-home`/`preview-tmp` under the attempt `audit` directory, chowned to the preview UID, plus a root-only `0600` launch config.
5. Spawns the reviewed shared launcher by absolute path. The launcher applies a parent-death signal, process limit, `no_new_privs`, drops all groups and every UID/GID slot to the preview identity, clears all capability sets and verifies them, then `exec`s the pinned interpreter and server.
6. Waits for the health endpoint, requiring the reported service, project and PID to match, and verifies `/proc/<pid>/status` shows only the preview UID — i.e. the drop actually took effect rather than being assumed.
7. On any startup failure, stops the UID through the stop gate before raising.

`PreviewHandle.stop` stops every process of the preview UID and reaps the child, so a completed stop is observable. `resource_ids` for the caller's combined proof come from the stop gate, not from this module.

## Refusals

Missing or non-absolute launcher/interpreter/server, relative or non-`Path` denied paths, a project outside the attempt, symlinked or hardlinked markers, a project the preview cannot write, an ancestor it cannot traverse, a missing audit directory, a listening port, ledger errors, an unverified or reserved UID, a 65534 UID, or a preview UID equal to the Pi UID. On non-Linux hosts or a non-root caller the module refuses immediately and never touches the ledger.

## Limits

Traversal and writability are checked from mode bits only. A deployment that relies purely on ACLs must also grant the equivalent mode/ownership bits or the launch is refused — this is deliberate fail-closed behaviour, not ACL emulation. The shared launcher's capability ceiling and the parent-death signal are inherited from the reviewed shared runtime; this module adds no cgroup, network namespace or filesystem sandbox of its own, and a distinct UID plus private HOME is not a full security boundary.

The preview server itself, its rendering fidelity and the visual-review workflow are unchanged and still need the existing independent checks. `StopProof` produced downstream remains an in-process contract, and collection still requires both UIDs to be proven stopped.

## Verification

Windows ran 15 cases with 11 passing and 4 platform skips (deployed image paths, POSIX traversal, two real Linux launches).

The parent independently ran the preview suite together with the stop gate on Linux in the pinned PPT base image `sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a`: **28 cases, zero skips, 1.245 s, all passing.** The container was `root` with only CHOWN/SETUID/SETGID/KILL, `no-new-privileges`, read-only root and source, no network, private tmpfs, `--rm`, and was confirmed absent afterwards by exact name.

The real cases start the actual official server through the launcher, assert the live process runs only as the reserved preview UID, check health identity, confirm the launch config is `0600`, confirm a second attempt cannot write the project, and prove the stop gate reaps the process with `SIGKILL` (`returncode == -9`) leaving no process of that UID.

Not covered: rendering, visual review, model calls, network isolation, cgroup limits, container teardown, unkillable processes, power loss, multi-node storage, or any publication/acceptance gate. This module is not wired into dispatch and grants no production readiness.
