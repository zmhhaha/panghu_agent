# Cold admission lease repair

On 2026-11-26 (Asia/Shanghai), two explicit WikiSkill publications uploaded and
submitted successfully under v4 compilation revision 2, then failed before any
native model admission. Their original event is `lease_expired`; the terminal
`quarantine_stopped` code records safe physical cleanup.

The latest publication is `4379470b-521b-436b-850c-5f6d6a800175`, remote job
`a7999d2f-466f-48ba-8d86-e6f65bb2089e`, attempt
`af47d041-4c4b-4c0a-9ccd-399fe35ce545`, fence 645. Its 1,892 inputs total
64,092,827 bytes. Allocation consumed about 43 seconds of the initial 60-second
lease; the cached Pod started in about two seconds. `worker_start_claimed` stayed
false. Both failed attempts have zero model requests and no artifacts.

## Repair

The dispatcher uses ordered batches of at most 500 input IDs, with owner,
tombstone, original-byte hash and path uniqueness validation. A new server-only
dispatch-intent marker records the preparing lease interval; historical attempts
do not gain new renewal authority.

Every five seconds during preparation, the exact live Kubernetes attempt may
renew its bounded lease while native start remains unclaimed. Cancellation,
expiry, a stale fence, the final deadline or native admission refuses this path.
Native admission atomically refreshes the original bounded lease and sends that
lease with the one authorized PUT. The immutable request hash excludes lease
time and stays unchanged. Lost responses inspect the same attempt; they do not
authorize another PUT. Running workers still require successful observation for
their existing renewal path.

The final task deadline is independent of a renewable lease. WikiSkill currently
allows at most 900 seconds of native compilation and 1,200 seconds for publication,
with a 250-second collection reserve. Upload and admission consume part of the
available window. This repair does not extend frozen deadlines or fabricate a
release from a failed attempt.

## Verification

Six relevant suites initially passed 188 tests with one existing fixture skip.
After tightening historical-intent and short-lease boundaries, the two affected
suites passed 87 tests. These are overlapping runs, not additive totals.
`git diff --check` and compilation checks passed.

The tests cover 225 seconds of slow input preparation plus repeated 45-second
cold ticks; a single Pod and native admission; fresh unchanged-hash start leases;
lost PUT responses; cancellation, expiry, stale identity, historical/static
attempts and unobserved admitted workers; and batch boundaries, ordering, owner
isolation, tombstones and forged original bytes.

Original read-only measurement used 1,892 separate input reads: 16.743 seconds
for envelope preparation, including 16.064 seconds in database reads. Three
identity hashes each cost about 0.87 seconds and reproduced the original
`9119ddcce5c8b8035d161bff8c59329575b5bb818cd208fac60c63ce29517a3c`.
The read-only timing omits advisory lock contention and is not a native authoring
acceptance test.

## Delivered verification and rollout

The exact production role source, based on the concurrently delivered
`8f2abd3ad94fd09a47c4a92033da9e981c9e22ed` stop-proof repair, passed all 193 relevant
tests in its actual Linux image. Existing `_stop_proven`, result, cleanup and
quarantine reconciliation methods were independently compared by AST and
preserved. The earlier local overlapping test runs are not counted again.

The actual 1,892 frozen inputs now prepare in 2.446 seconds with 11 SQL statements,
including read-only transaction setup, and reproduce the original request hash.
The comparison still omits advisory lock contention. A separate actual Pi image
probe used the complete original inputs with an injected offline driver:
`WorkerHost.start` took 2.845 seconds, staging and real profile validation reached
the fixture driver after 17.565 seconds, and three renewals took 2.69–3.08 seconds.
It retained over 54 seconds of worker lease after each renewal/progress pair,
peaked at 766,693,376 cgroup bytes and recorded zero OOM events. This verifies
large-input host preparation and lease I/O, not a real native authoring result.
There were zero native model calls, production jobs or knowledge publications;
temporary fixture state was cleaned.

The source patch is `176d2a28aad1648887e7b45f0216b6bfc38395f8`.
Its exact production role revision is
`24940de733ce4a4eea2f597297dd701838fbb0d1`, deployed using
`arm-cluster-master:5000/agent-service-control:latest@sha256:e026d8e4d309216912d04e9f4b062157faad45759c9183e0e871dab9e10117c6`.
The guarded rollout checked physical quiet, empty occupied slots and unretired
allocations, stopped worker Pods, no pending publication, and Deployment
UID/resourceVersion/full spec before replacement. API, model proxy, executor,
lab and the public catalog retained their pre-rollout specs/identities.

All five WikiSkill publication-table fingerprints match before and after:
12 original jobs, two releases, and active release
`bccef9ca-433d-46f4-bfc5-ac638db5fbbd` remain unchanged. Both failed attempts remain
terminal with original snapshots and receipts; neither has a recoverable output.
The repair did not retry the author or activate new knowledge. A later explicit
publication uses the repaired cold-admission path.

Remote build and rollout evidence:
`/root/video-releases/cold-admission-20261126-r2/`.
The first prepared image was retained without deployment after the rollout guard
detected a concurrent production source change; the delivered image includes
that newer baseline. A stale historical-exception preflight was replaced by a
strict current-state quiet check after those old quarantines had legitimately
been resolved by the concurrent stop-proof repair. The new check has no
historical exception and changes no service records.

Operational evidence is retained at
`D:/codex-work/operations/wikiskill-publication-failure-20261126-r2/`.
OpenSpec change: `fix-agent-cold-admission-lease`, mapped project
`ec663f5a-53d3-4a0e-9037-933eb788d183`.
