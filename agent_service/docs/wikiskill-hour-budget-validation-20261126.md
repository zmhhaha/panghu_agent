# Versioned whole-library compilation budget

The user explicitly requested one new WikiSkill release after the cold-admission
repair. Publication `c383c252-2937-4735-9c58-71f234bfc7f5` entered native execution
and completed 35 model turns, including the complete survey and selected original
reads. It reached its original native deadline before writing a final artifact.
Remote job `f1546674-2291-4c38-8fe6-5c910e6823ac` stopped with `deadline_exceeded`;
the publication remains failed with its original inputs, clocks and stop proof.

## Separate compiler revision

Code commit `9f597f79021c6d0ee49a398d289eb8f7c20503e0` adds v4 compiler revision 3.
It keeps revision 2's 63 MiB snapshot and 64 MiB total input bounds, input/output
schemas, original record hashes, fixed tool files and Skill bundle. The new
native maximum is 3,600 seconds. Newly created publications freeze timing v2:
4,200 seconds overall and 450 seconds reserved for collection. Gateway requests
remain clamped to the available absolute budget.

Revision 1/2 and historical missing-marker paths keep their original semantics.
An existing 900-second failed request is never revived or given a longer grant.
Replaying its request ID returns its original outcome. A new explicitly
authorized publication receives its own request ID and frozen revision.

The ordinary model read/idle wait is independently 600 seconds; it is not the
same as the renewable 60-second execution lease or the complete task deadline.
Preparing heartbeats continue to require live, fenced, unstarted authority.

## Verification and runtime admission

Necessary regressions passed 276 tests with one existing skip. They cover new
budgets, remaining-time clamps, exact marker/revision matching, deadline expiry,
frozen legacy replay, idempotency, complete capacity and unchanged fixed tools.
An isolated short Windows temporary path resolved an environment-only path
failure without changing the relevant business assertions.

The complete execution chain was checked: public request/policy/contract bound
3,600; runtime buffered idle follows the profile; native requests and model grants
use the original absolute deadline; production Pod deadline is policy plus 90,
or 3,690 seconds; the publisher pump has no separate 900/1,200-second cutoff.

One genuine tiny revision 3 candidate completed six model calls, a valid latest
output check, stopped output evidence and an actual expired-grant HTTP 401.
Independent admission report:
`07a17b222258c3d2cfdaa13fce1c00547bd07b205e3aae662dc56fab60b33ceb`.
Accepted binding hash:
`f220a703ee066490996e01215f6a553e7385fc7a4b5706d7a26d9daed8ec0d1f`.
Combination:
`7a1a29af4c7283a18ca19a647b323978ef32f3637bf3d4ceeb2d8ff07190c0af`.
Technical artifacts were not imported into user knowledge, and exact candidate
resources were cleaned. This tiny probe does not establish full-library execution
or a one-hour wall-time run.

The first candidate launch failed before resource creation because a historical
UID reader did not recognize a new permanent receipt wrapper. A strictly bounded
compatibility reader preserved all historical ranges, rejected unknown formats
and left the original shared helper untouched. The failed preparation and new
operator revision remain separate evidence; no model call was duplicated.

## Exact deployment

A concurrent checked-source update changed all three control baselines and their
catalog. A drift guard blocked the stale rollout before production writes. The
three controls were rebuilt from their newest sources; the exact Wiki worker and
executor remained reusable. The selected Wiki Host was byte-identical and all
binding components matched the genuine admission, so the technical model probe
was reused without additional calls.

Delivered images, all built and pushed with `latest` and pinned by digest:

| Role | Digest |
| --- | --- |
| API | `sha256:d37ad4534112557f3d52221bc16a7b00a7a546897df3ee64b0936172c6aceed3` |
| Dispatcher | `sha256:b073e576c12a8a4b5d977c7623c4036b8d2847606857d5bc97ef5645d858776d` |
| Model proxy | `sha256:c1bfe2bc05b081fe958fa5730a9446ed4cf2836df4d31df4f5de2444e329a030` |
| Pi compiler | `sha256:20d2725b341eceaf84725c9300bb93f8f9439b0887bfd6a3d524bc13d3bfc911` |
| Publisher executor and init | `sha256:ef88cde80c3751b890022ba0845f61a9d0102f250f45ff2c3351424002e68f50` |

Catalog hash:
`a61e2b966bd90f9ccab67f465dd754a3a747d74d79d9e7aec98d778a8d7ad1f9`.
New binding: `binding-7a1a29af4c7283a18ca1`.
Production UID range `350065536/65536` is permanently reserved.

The rollout checked physical quiet, pending publications, historical Host identity
and full Deployment UID/resourceVersion/spec before every stage. Final actual
Pod image IDs and source inventories match. All 109 historical binding pins and
Host UID/data hashes, cold-admission/unscheduled-stop fixes, checked-source,
Canvas seek reset and lab spec are preserved. Gateway revisions 1/2/3 are ready
with native maxima 900/900/3,600. The actual new publisher selector and timing v2
match the reviewed implementation.

All five WikiSkill table fingerprints match before and after infrastructure
rollout: 13 original jobs, two releases and active sequence 2 remain intact.

## Authorized user publication

The fresh user publication is `815d1060-33e0-4d1a-becc-a998304abde5`, request
`92bd5b64-bb12-4d8a-8537-b194ccb0f0dd`. Remote job:
`349ff079-2379-45d1-8954-90b0f6b02301`; attempt:
`7ead5b71-5d3b-4209-bfde-b37642895e8b`, fence 647.
Its frozen revision 3, 4,200-second publication and 3,600-second native windows
were confirmed. It published successfully at 13:29:08 Asia/Shanghai on 2026-11-26.
Release sequence 3 is now active:
`384d6c40-ba43-4056-8b59-860e5b7f7b41`.
Release SHA:
`cd60d4737d1ff2560cd97ea2e921965b14b8bc513b16a4b41ab06b0c496bf585`.

The successful attempt completed 44 model turns, all HTTP 200. Publication took
1,174.63 seconds (about 19 minutes 35 seconds); native creation to proven stop took
1,067.16 seconds, beyond the former 900-second ceiling and within its frozen
3,600-second limit.

Independent read-only verification recomputed the original 1,891 records, 1,892
input files and 64,092,828 bytes against the snapshot, manifest and input receipts.
Coverage is complete and disjoint: 45 records were adopted as evidence and 1,846
have explicit exclusions. Coverage does not mean every historical record was
promoted into a Skill. Six packages each contain one `SKILL.md`:

- `animation-authoring-foundation`
- `causal-comparison-and-arrival`
- `spatial-trigger-coordination`
- `ink-structure-and-reveal`
- `visible-state-handoffs`
- `enclosure-and-depth-reveal`

Every package, file and complete release hash matches. Authenticated Agent GET
recovered the same completed attempt, stop proof, artifact manifest and exact
685,374-byte result used to publish the release. Artifact SHA:
`e359dfb191b1cd4be34c4cecfb8e2344c6d23931cbd742f4902c624774ce63a0`.
Artifact manifest SHA:
`825114863e58e8a4b106b0a3b127ec5a3b00910e75650b021cda125c81a842fe`.

The active pointer matches sequence 3. Excluding the new rows, all prior 13 jobs,
13 snapshots, two releases and activation records retain their exact fingerprints.
Current totals are 14 jobs, 14 snapshots and three releases. No history was
deleted, and neither failed attempt was revived. Verification used GET/read-only
SQL, added no model calls or publications, and its observer exited normally.
The release retains `frozen_records_only_no_new_media_validation`: this is
knowledge compilation and structural/provenance validation, not new visual,
audio or artistic approval.

Evidence: `D:/codex-work/operations/wikiskill-hour-budget-20261126/`, particularly
`independent-release-verification.json`, `user-publication/release-metadata.json`,
`user-publication/catalogue-after.json` and `final-runtime-verification.json`.
Final remote release:
`/root/video-releases/wikiskill-hour-budget-20261126-r2/rollout-final/`.
OpenSpec: `extend-wikiskill-compilation-hour-budget`, mapped project
`ec663f5a-53d3-4a0e-9037-933eb788d183`.
