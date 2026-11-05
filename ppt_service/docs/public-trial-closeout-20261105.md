# PPT public trial closeout — 2026-11-05

The owner moved shared production security, recovery, capacity and continuous
backup work into the common framework for scientific, PPT and video services.
OpenSpec `harden-shared-platform-production-readiness` now owns the original
PPT R3–R6 scopes as P1–P4. They remain incomplete with their original evidence
and limits. See [the platform handoff](../../agent_service/docs/SHARED_PLATFORM_READINESS_20261105.md).
Both the new platform change and updated PPT parent validated successfully.
The PPT parent now has 145 completed business tasks out of 146; R1 remains open.
Transferring four tasks did not mark them passed or remove existing protections.

## Public trial site and Git delivery

The fixed “开发测试中” badge and its unused CSS have been removed. The existing
trial feedback notice and development-identity-only notice remain. Commit
`0c8199c` contains the UI change; merge `bebe01d2320242a2bb7c3efa6082f13dd1965654`
preserves the current main's platform/video changes. Both the feature branch
and application `main` received this merge through normal Git push.

The server fetched that exact main revision, built and verified the site,
published `latest`, and rolled out only `ppt-site`. Receipt:
`/root/ppt-public-trial-closeout-f56e7bff/receipt.json`.
The image is
`arm-cluster-master:5000/ppt-service-site:latest@sha256:bf52c652f1f99d199a797663b2d61f796312aa85f106be3be4cb09ed4b5d9c17`.
Readiness and compiled-content checks passed; the signed-in website also showed
the trial notice without the development badge. There were no model calls,
business-row edits, schema changes, or other service rollouts for this UI release.
All application building and verification ran on the server.

## Actual downloaded-chart Office operation

The exact downloaded NASA revision `24960dcf-55cf-46bf-a631-0ecc6590f302`,
SHA256 `9593b42a0bedaaf91f9d8c7002972138abcae1ff90dcb14513c29941dcff6669`,
was opened in actual desktop PowerPoint 16.0. In an independent working copy,
the embedded workbook's B2 changed from 0.98 to 0.99. Explicitly selecting the
same original data range with `Chart.SetSourceData` synchronized the native
series; `Chart.Refresh` alone had only redrawn its existing cached values.

The saved file was closed and reopened. The embedded workbook, native chart
series and saved OOXML chart cache all contained 0.99 followed by the unchanged
1.01, 0.85, 0.89, 1.17 and 1.28. The original three cell-range formulas and six
pages remained. The downloaded original stayed byte-identical, owned Office
processes exited, and the edited verification copy was not uploaded.
Receipt: outer workspace
`.research-temp/ppt-nasa-office-20261105-r4/receipt.json`, `passed=true`.
The first three helper attempts remain separate failed receipts; the earlier
workbook-only check did not prove chart-cache synchronization. This downloaded
product operation is not a local application test, build or render.

## Corrected strict-acceptance status

Earlier source-chart artifact readback proved exact source values, native chart
and workbook content, uploaded PNG bytes, notes and immutable versions. Its
`passed` field did not attest all seven strict programmatic acceptance gates.
The signed-in website correctly showed `needs_changes` for revision `24960dcf`.

A bounded read of its retained report found zero errors and three introduced
warnings on each of six pages: missing root-group `data-pptx-bounds`, a split
two-line source paragraph, and missing root `data-pptx-page-role`. The 18
warnings prevented source-quality and complete export/delivery acceptance.
These checks were not weakened or relabeled as passed.

One ordinary website revision, `4605df6a-fdb7-43a7-addd-8e3ad5f73d46`, was
submitted against that explicit original draft to repair only those source
structures while preserving visible content and the frozen chart binding.
Its completed outcome is recorded below. The two original drafts,
uploaded files and existing evidence remain unchanged. No owner visual
declaration or publication has been inferred from these operations.

## Completed strict source-structure revision

Website revision job `4605df6a-fdb7-43a7-addd-8e3ad5f73d46` succeeded and
produced review Version `e81f58f1-f589-4236-b0d2-3ea04e0daedb`, parented by
`24960dcf-55cf-46bf-a631-0ecc6590f302`. The exact downloaded PPTX SHA256 is
`bf6521cec2c063e14b93315323208f13416f04d57ff73aee3151b8cf711c5b24`.
All seven strict programmatic acceptance gates passed without weakening
the checks. The earlier drafts and their original evidence remain retained.

Read-only server comparison confirmed unchanged chart values, embedded workbook,
uploaded PNG, notes, exact visible character sequences and all six final preview
hashes. The initial paragraph-array comparison failed because the intended source
repair combined each two-part footer into one paragraph. That original receipt
remains unchanged; the separate structural comparison verifies this exact change:
`/root/ppt-chart-artifact-read-cffb9fb26f9b/structure-preservation.json`,
`passed=true`. No new generation or business-row writes were used for this readback.

## Actual Office operation on the corrected Version

The exact corrected download `e81f58f1` was opened in actual desktop
PowerPoint 16.0. It has six pages and one native chart on page four. In an
independent working copy, the embedded workbook's B2 changed from 0.98 to
0.99, and the chart was rebound to its original source range.

After saving, closing and reopening the working copy, the embedded workbook,
native series and saved OOXML chart cache consistently contained 0.99 followed
by the unchanged 1.01, 0.85, 0.89, 1.17 and 1.28. All three original cell-range
formulas remained unchanged. The exact downloaded original retained the SHA256
above, and the Office processes used for this operation exited.

Receipt: outer workspace
`.research-temp/ppt-nasa-office-e81-20261105-r1/receipt.json`, `passed=true`.
This result belongs to the corrected Version itself; it does not rely on the
earlier `24960dcf` Office result. The operation used a separate verification
copy and is not a local application test, build or render.
The actual desktop result was also saved through the normal website's desktop
check form and read back as passed. No owner visual declaration was saved.

All seven strict programmatic gates and actual Office operation now pass for
this corrected review draft. The owner's NASA visual confirmation remains
pending, no publication has been recorded, and the PPT parent remains 145/146
with R1 open. Shared platform P1–P4 continue in their independent change.

## Owner visual confirmation — 2026-11-05

After the preceding pending checkpoint, the owner explicitly replied
“我已经视觉确认，还是不错的” to the request concerning the six-page NASA
review Version `e81f58f1-f589-4236-b0d2-3ea04e0daedb`. This is the owner's
actual visual confirmation for that Version, alongside its seven passing
strict programmatic gates and exact-version PowerPoint operation above.
The visual check was saved for this exact Version through the normal website
at `2026-11-05 20:02:47 Asia/Shanghai`, checker `27714443`, review revision 2.
The original desktop-operation record at revision 1 remains retained. The
website shows independent programmatic, per-page visual, PowerPoint desktop
and complete acceptance as passed.

This completed R1 and the PPT parent's 146/146 tasks. OpenSpec validation passed
without issues, and its normal archive operation succeeded as
`2026-11-05-add-hermes-ppt-workbench`, with `specsUpdated=true` at revision
`92088c7c6cbcadceb76d17e5f65e3fc569fbbe96`. The active change list no longer
contains the parent. The shared platform change still has its four P1–P4 tasks
open. The independent website readback is retained in outer workspace
`.research-temp/ppt-nasa-owner-visual-20261105.txt`.

The NASA Version remains unpublished. Visual confirmation does not authorize
formal publication, and no such authorization is inferred. Shared platform
P1–P4 remain independently incomplete.
