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

Operational evidence is retained at
`D:/codex-work/operations/wikiskill-publication-failure-20261126-r2/`.
OpenSpec change: `fix-agent-cold-admission-lease`, mapped project
`ec663f5a-53d3-4a0e-9037-933eb788d183`.
