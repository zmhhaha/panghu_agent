# Native Pi fixture start/stop controls

This small server-only probe fills one explicit evidence gap: the currently
selected immutable Pi worker image really starts native Pi and reaches a model
endpoint through its image-owned `PiRuntime`, `DomainExecutionProfile`,
`PiDriver`, `WorkerHost`, and UID/StopGroup controller. The endpoint is a clearly
labelled deterministic HTTP fixture. It holds the native request before any
response/tool execution; it never calls a supplier or generates a PPT.

This is **not complete R5 acceptance**. It does not prove full PPT generation,
current PostgreSQL/Dispatcher recovery, native OOM, node/Pod crash recovery,
network/deadline recovery, stop uncertainty, ENOSPC, quality/render failures, or
availability of an old business Version. Those need their own applicable
receipts. Existing synthetic kernel/control tests retain their original limits.

## Scope and safety

- An administrator supplies a permanent append-only reservation for 32 fresh
  UIDs, verified against the shared ledger, passwd/groups and live `/proc`.
  The existing recovery reservation reader places an O_EXCL consumed marker;
  failed runs never reuse the range.
- Every resource is created in a unique previously absent namespace. The
  namespace UID, owner label, original Pod UID, containerID/imageID and node are
  bound before any trigger. There are no host mounts, service account tokens,
  production Secrets, upstream credentials, PVCs, accepted-host records, routes,
  shared databases, image builds or downloads.
- The fixture credential is new random data. Both namespace egress and ingress
  start denied. The worker can reach only the fixture Pod's TCP 8080. The SAME
  fixture also listens on 8081, which the worker must fail to connect to, so the
  deny check has a known listening positive control. A public-IP HTTPS failure
  is recorded as an observation without a healthy public positive control.
- The candidate factory measures real isolation and image-owned resources.
  Its operator catalog and routes remain disabled. Local WorkerHost admission
  is confined to the private administrative process and creates no production
  acceptance artifact.
- Each case submits `host.start(envelope)` twice, waits for exactly one genuine
  native model POST, and then triggers either normal WorkerHost cancellation
  or pidfd SIGKILL of the exact reserved-UID image-owned native Node processes.
  UID/GID/groups, empty capabilities, NNP, seccomp, starttime, executable and
  cmdline hash are checked; pidfd opening has identity checks on both sides.
- The real StopGroup must prove stop, and the WorkerHost signed stop receipt is
  verified against the original envelope. Raw harness records are private.
- Cleanup uses original UID preconditions and complete GET observations. Pod
  absence is API cleanup evidence, not independent kernel stop proof. Any
  uncertain identity/stop/cleanup retains the namespace and returns failure.

## Invocation after review and Git push

Run the committed operator from its exact Git worktree on the Linux server.
The reservation is produced only after the administrator reviews this runner;
it must satisfy the existing `ppt-disposable-recovery-v1` receipt contract.
The independent reservation tool checks the actual selected Node bootID against
the local kernel before trusting its passwd/group/process scan, and records
the Node UID and kube-system namespace UID. All commands use the fixed
`/etc/kubernetes/super-admin.conf` context. The outer runner checks those IDs,
bootID and actual Pod nodeName before native triggers.

Coordinate an exclusive ledger-writing window with other deployment operators
first. The shared `uid-reservations.lock` protects only cooperating writers;
it is not cross-writer CAS and cannot make an unlocked legacy writer safe.
The tool records this boundary and refuses to run without the explicit
`--exclusive-ledger-window` operator assertion. It never releases ranges or
removes pre-append intent exclusions, even when a subsequent probe fails.

```bash
python3 ppt_service/tests/e2e/reserve_native_pi_fixture_uids.py \
  --source /root/ppt-native-source-<git-sha> \
  --output /root/ppt-native-uid-<unique-run>.json \
  --node <the-current-local-kubernetes-node> \
  --exclusive-ledger-window
```

```bash
python3 ppt_service/tests/e2e/run_native_pi_fixture_kubernetes.py \
  --source /root/ppt-native-source-<git-sha> \
  --output /root/ppt-native-stop-<unique-run> \
  --uid-reservation /root/<private-new-reservation>.json \
  --image arm-cluster-master:5000/agent-service-pi:latest@sha256:<reviewed-current-digest>
```

The operator records the actual worker image and image-owned module hashes.
Its fixed `native-fixture-20261104` runtime/catalog identity and fixture model
selection are explicit administrative candidate pins, not production host
acceptance. Do not relabel the results as a real supplier/model evaluation.
