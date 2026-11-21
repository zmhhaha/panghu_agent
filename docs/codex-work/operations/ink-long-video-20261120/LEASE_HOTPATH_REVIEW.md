> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/LEASE_HOTPATH_REVIEW.md`；原文件 SHA-256：`bb3291a9e81c88f5ab64617e38b5a04556ff51fc0c3d5248436ee6d6a6d747c6`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

Read-only independent review of the second publication lease failure.

Original recovery: job `0f4c9d33-517e-4516-b303-165eee98a724`, attempt
`0422e1b1-5b33-40c6-b1f6-44ad24c76a2d`, fence 449. Its original allocation
request digest is `b5f830e453e6bce0139e4380908d385ca9af690bcc86eed57ef8551ba6066323`.
The original attempt is failed with positive stop proof and zero model requests.
No attempt, lease, input, allocation, or result was modified during this review.

The controller repeatedly reconstructs content while trying to deliver a
small lease message. Dispatcher `_envelope` loads 1031 inputs through separate
Store transactions. Every Kubernetes provider `_allocation`, including inspect,
renew and events, calls `attempt_provisioning` -> `_matching_worker_request` ->
`_worker_envelope`, loading those inputs again. `_manifest`, credentials, status
and receipt binding repeatedly evaluate `ExecutionEnvelope.request_sha256`,
which serializes the complete inline-file identity each time. `_worker_envelope`
also holds the Store's global transaction advisory lock across its input loop.
Content reconstruction therefore delays both control delivery and other Store
transactions; renewing the DB lease does not prove that the worker received it.

Actual original-input measurements by the implementation agent in the API pod:

| Operation | Seconds |
| --- | ---: |
| Dispatcher `_envelope` | 8.488 |
| Store `attempt_provisioning` | 5.376 |
| Provider `_manifest` | 2.438 |
| Provider `_allocation` | 7.698 |

One envelope reconstruction and three allocations already consume over 31
seconds before transport, additional identity computations, worker state writes,
event pagination, or contention. More event pages repeat allocation again.

My independent measurements in the actual dispatcher pod used the exact
original job's 1031 inputs (29,680,163 bytes), with PostgreSQL transactions forced
READ ONLY. The reconstructed identity was 39,708,098 bytes and matched the
original persisted allocation digest. Each SHA computation took 0.413–0.439
seconds. A single bulk SELECT read all inputs in 0.929 seconds. A separate
temporary SQLite simulation of worker renew persistence took under 0.75 seconds
for deepcopy, full-state canonicalization and commit. That last figure is a
dispatcher-container lower-bound simulation, not a measurement of a live worker
endpoint. It does not justify changing the worker image before fixing the
controller's repeated payload reads. Exact evidence is in
`lease-hash-and-worker-write-benchmark.json`.

The smallest reliable fix is an internal `ExecutionControl` recovered from an
already fully verified allocation after native start was durably claimed. It
contains the original measured request digest and bounded control metadata;
the public mutable ExecutionEnvelope has no cached digest. New dispatch intents
freeze request_hash, tenant and caller in addition to existing pins/deadline.
Each control operation rechecks that intent against the current job and the
exact attempt/fence/allocation. Legacy records without the new frozen fields
fall back to original full-envelope verification. This works after a controller
restart without relying on an in-memory cache.

The reviewed patch preserves these boundaries:

- Full source/checkpoint bytes remain verified before original reservation and
  the only native PUT. A compact control object cannot reserve, create, or start
  a worker. Provider and transport explicitly reject it for start.
- Compact control requires create_claimed, worker_start_claimed, and a bound Pod
  UID. A missing physical resource does not authorize recreation.
- After an inspect call claims the first cold native start, the same dispatcher
  poll immediately switches to compact control before remote renewal/events.
  This avoids spending another large-input verification interval before the
  first lease delivery.
- Mutated request contents, owner, request hash, pins, deadline, job/attempt/fence,
  artifact limits or input-presence flags fail the authoritative comparison.
  Nested binding mutation is detected by that comparison; no public wire-object
  cache can preserve an obsolete digest after mutation.
- Store lease renewal still rejects stale/expired attempts. Cancel and quarantine
  recovery retain exact Pod UID checks, one-start claims, stop evidence and
  finalizer semantics. Compact metadata does not extend a failed transport's
  lease or convert missing-resource evidence into stop proof.
- Receipt verification and artifact limits use the same original identity and
  bounded limits. Historical routes and legacy full-envelope fallback remain.

The requested owner binding and physical-create guards were added during
review. No further code blocker was found in the final diff. Required rollout
evidence is a real large-input attempt with successive worker lease deliveries
and actual model execution; passing static tests alone cannot establish that
the production timing problem is resolved.
