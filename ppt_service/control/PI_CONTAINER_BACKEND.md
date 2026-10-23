# Offline Pi container lifecycle

Updated 2026-10-23. The new backend is explicitly `productionUserTasks=False`; it runs the fixed offline domain probe, not a user PPT workflow. No external model, public task route or publication is enabled.

## Host authority

`pi_container_backend.py` provides create/observe/heartbeat/stop/recover/collect/cleanup for an exact Attempt and Layout. Before Docker create, it verifies both writer UIDs against the host durable allocator, writes an exclusive root0444 domain-launch.json into the RO task mount, and fsyncs the root0700 container-authority directory plus root0600 control.json. Every update uses a root-private flock, exclusive temporary file, file fsync, atomic replace and directory fsync. Existing authority is never overwritten as a new attempt.

The protocol binds schema, full project/job/attempt/token/phase identity, random nonce, Pi/preview UIDs, host boot UUID, image ID, monotonic deadline, incrementing sequence, sticky revoked/state and exact container ID. Starting intent is persisted before Docker start; returned CID is persisted before start. A recovered controller never starts the old attempt. Unknown-CID recovery searches all exact nonce/identity labels and refuses zero, multiple or foreign results; it does not infer stopped writers from an empty listing.

Containers are created without auto-remove or restart, preserving inspect evidence until cleanup. Inspection requires exact CID/image/backend-label namespace, private PID namespace, restart=no and AutoRemove=false. Image-inherited labels outside the backend namespace are allowed. A full-label equality initially assumed by the prototype would incorrectly reject the image's inherited offline labels; the final implementation and regression distinguish inherited labels from authority labels.

## Worker lease

`worker/container_lease.py` reads only the fixed private host control path. Immutable launch metadata is required in --host-controlled mode; missing metadata cannot fall back to standalone test IDs. Parents/root ownership, exact file mode, single hardlink count, no-follow file access, bounded strict JSON, known field set, matching identity/nonce/UID/image/boot and monotonic deadline are checked. Equal sequence requires equal content; regression, mutation, missing record, expiry or revoke irreversibly fences that reader.

The monitor renews the in-container ModelGrant only on a newer valid host sequence. Revoke/expiry closes actual model sockets and sets the PiRuntime cancellation event. The same Linux kernel boot and monotonic clock are required; restart does not revive an expired authority. The explicit hold fixture pauses the first synthetic model response so cancellation is tested against an active real Pi request rather than a completed command.

The host checks physical running state and old deadline before and after the committed RepositoryQueue heartbeat. New deadlines are capped by controller configuration, handle TTL and queue TTL; transaction latency is deducted by calculating the deadline before the DB call. The same conservative timing correction is applied to TaskModelProxy. Callers must provision the initial lease no longer than the owning queue's lease; live-driver provisioning remains pending.

## Stop, collect and recovery

Stop durably revokes first, then stops/kills only the exact verified resource. StopProof is true only for inspected Status=exited, Running=false, Restarting=false, Pid=0 with the expected identity/image/labels. Paused/dead/unknown state, unsafe restart policy, foreign metadata, unavailable Docker or an ambiguous create produce no usable stop proof. Some never-started/ambiguous-created resources deliberately remain unproven for reconciliation rather than being treated as successful shutdown.

Collection revalidates the proof, sticky stopped/revoked receipt and fresh Docker inspect before freezing project/result. Cleanup repeats those checks and removes only that CID with its anonymous volumes. A caller-supplied true flag or idle UID scan is insufficient. Queue slot release is still a separate fenced Repository operation after proof. No candidate becomes a Plan, Version or published checkpoint merely by being frozen.

## Evidence

Final local backend tests:30passed,1native-Linux ownership skip,1.23seconds. Lease protocol tests:Windows7passed/1skip; independent Linux8/8pass0skip0.026seconds, including mode/hardlink/no-follow checks. The test subagent installed pytest into the existing default local Python to run the backend suite; no server package installation was needed.

Real integration used a disposable PostgreSQL16 database on an internal Docker network (no host port), a trusted controller container with Docker socket/client access, and separate actual-Pi containers with network=none and no Docker socket/DB credentials. Only the trusted controller could provision/inspect resources; Pi child still had its separate UID, zero effective capabilities and RO domain mounts. Fixture UID ledgers and ranges are isolated test data, not production host-wide pool reservation.

Four actual-Pi + PostgreSQL + Docker cases passed,0skip,32.017seconds:

1. Successful fenced heartbeat, real fixed tool read/write, exit0, exact stop proof, immutable private candidate, DB state validating and no Plan/Version.
2. A committed DB cancellation revoked the silent model stream and actual Pi, observed pi_error=cancelled and internal stop proof, then external container proof permitted DB cancelled state.
3. No controller renewal caused the10second lease to expire; actual Pi exited2, model grant inactive, later heartbeat rejected, no result artifacts, and physical proof preceded terminalization.
4. A fresh backend instance recovered from the durable receipt, stopped the active attempt without start/resume, and rejected the previous controller's later heartbeat.

The first matrix had3lifecycle cases pass but normal completion failed an obsolete standalone30second connector-timeout assertion. The host-controlled check was corrected to the bounded host lease model, and all four were rerun successfully. No failed run is counted as passing. Logs are retained before proof-gated resource removal.

Full successful matrix code archive:8c2aac197815e0647c57fbcb1bbc021e9da96ef41d9f5a8c94c5ba003103b79a (942080bytes), root `/root/ppt-pi-container-lifecycle-20261023-a2/`, with lifecycle-results.json and per-container logs. The final commit-latency cap change has its own archive6b8f0aca074b6f64c4ada3f6c927e25566b4f2a1b1e248651a20ed76f75dc6c6 (942080bytes), root a3: targeted normal heartbeat/collect integration1/1pass7.139seconds, and TaskModelProxy real-PG regressions11/11pass4.110seconds. These retain matching source snapshots rather than overwriting the earlier evidence.

## Still pending

This is a verified physical lifecycle for a fixed synthetic workload. PptProfile production admission, actual task-context reconstruction in the mounted container, real supplier model/vision, live PiDispatch and UI/API routing, independent PPT validation and PowerPoint manual acceptance remain pending. Hermes default routing, existing UI/two confirmations and publication gates are unchanged.
