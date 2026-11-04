# Owner history and independent-render handoff — 2026-11-04

Scope: continuation of `complete-ppt-p0-p1-operational-acceptance`, especially
normal-owner C5/C10 and the ten-page text-layout revision. C10/R7's real owner
history scope is complete. The targeted normal-owner correction Job
`76fa036e-7f02-47c4-9f99-4c05d85dfe9d` has succeeded and produced review Version
`6f7c220f-01a6-4ab0-8703-180f091658d9`, explicitly parented by
`1c39b80c-287f-4186-a8de-7424da2a404d`. The exact owner download, all ten
receipt-bound final images, unchanged content/notes, old-version preservation
and seven completed aggregate programmatic gates now supply C5/TL7's scoped
completion evidence. R8's actual human declaration, R9's PowerPoint operation
on this production Version and R10's formal publication remain unsigned and
unperformed. Earlier failures, the `1c39b80c` partial receipt and running
observations below remain historical evidence; the final dated section records
the completed correction without rewriting them as passes.

## Real owner attempt and retained baseline

Project: `c9d480b5-be18-46ed-839b-e65a9bd57eda`.
Explicit baseline Version: `7ee06cd9-ea01-40a1-8048-532f6ef71434` (ten pages),
from successful earlier Job `db076cff-ada0-484b-9848-4caa0704c362`.
The previously captured baseline PPTX is 53,781 bytes, SHA256
`fc620aa89f92e85486cfbd05fd0a3751247c315b14870af24dede769c7522d79`.
Its source lineage and prior evidence are recorded in
`authoring-runtime-20261102.md` and `text-layout-version-selection-20261103.md`.
The prepared whole-deck feedback preserves ten pages, the corrected page-three
relationships and the original source facts while addressing text-frame defects.

The authenticated owner submitted revision Job
`b7fe65d5-ab02-4dc2-86fc-89b8917dae35` against that explicit baseline. The task
stopped with `execution_needs_input`; it did not deliver the required successor
Version. The Worker interpreted the prompt as requiring final-PPTX Office
rendering inside its authoring environment. That environment does not own the
independent Office renderer. The resulting request for an unavailable tool was
a misplaced responsibility, not evidence that the later quality gate passed or
that the owner should install Office in the Worker.

This failed attempt remains historical evidence. It must not be rewritten as
success, silently resubmitted or counted as the completed ten-page revision.
The baseline remains the reference; its download/hash must be checked again
after a successful bounded retry. No formal owner acceptance or publication is
recorded here.

## Read-only owner task history

The project now has a bounded task-history read contract:
`GET /api/v1/projects/{project_id}/jobs` returns at most 20 existing safe Job DTOs,
newest first by `created_at DESC, id DESC`. The query requires a non-deleted
project and matching project/job ownership. Missing, deleted and other-owner
projects return the same 404 behavior. Job time, safe error and applicable
quality-diagnostic projections match the existing single-job API.

The frontend adds a collapsed “最近任务” section below the current task. Opening
it reads the project list; choosing a row displays its human-readable task type,
status, recorded dates and read-only details. Existing safe error, task-time and
quality-diagnostic components are reused, including same-origin diagnostic
downloads. A full page reload can fetch and reselect an older failure even after
another task becomes latest.

Historical selection has separate component state. It does not replace the
active job, subscribe to historical SSE, start polling an old terminal job or
offer retry/cancel/generate actions. Access/read failure clears previously shown
history, and project changes abort obsolete reads and ignore late responses.
These are task records, not usage, billing or service-eligibility accounting.

Backend tests cover bounded ordering, owner/project isolation and historical
failed-task diagnostics. Frontend/API contracts cover expansion-only reading,
reopening and reselecting an old failure, escaped diagnostic text, unchanged
active task/SSE with no mutation, 401/404 removal and stale-request cancellation.

## Responsibility fix and exact source

Application commit `dd50e900b30a2a468bdcde84062093370725a93b` corrects the authoring
prompt boundary. Generation/revision must still render and actually inspect
every source-slide PNG, perform the required official authoring checks,
finalize/native export/postflight and hand off the exact candidate. Missing a
required authoring tool or a real approval remains a blocker.

After the candidate stops and is handed off, the trusted independent validator
renders that exact delivered PPTX using its fixed Office environment and checks
final-PPTX text layout. This remains a required rejection gate. The authoring
Worker must neither return `needs_input` solely because Office/LibreOffice is
absent locally nor invent a passing independent-render report. Source previews
alone still cannot attest the downloaded native PPTX.

Commit `e92124b5ec57aabee49bf6d1dff4db9625fc3968` contains only the final history
JSX type repair over that commit: boolean `!!error` guards the rendering of the
`unknown` error value. It does not alter the prompt, backend contract or quality
gate. The initial site build's TypeScript failure is retained; it is not relabeled
as a passing build.

Actual signed-in history inspection then found that the server's `revise` kind
was missing from the frontend label mapping. Commit
`e65a71f56d28ba7d1be665f8d7e14be903a7935e` adds `revise` → “按反馈修改”. Its
contract fixture now uses the real `revise` kind/stage and checks the active
card, history row and selected detail names while retaining active-task/SSE
isolation. This is a presentation correction; no backend kind or stored job is
rewritten.

## Confirmed server receipts

All application tests, builds and runtime probes in this round run on the ARM64
server from Git-frozen source. No local test/build/render result is claimed.

| Exact source or role | Evidence | Confirmed result and limit |
| --- | --- | --- |
| `dd50e900b30a2a468bdcde84062093370725a93b` regression | `/root/ppt-prepare-task-dd50e900b30a-6f2d331d40-checks/summary.private.json` | All 32 stages and 1,308 tests passed; cleanup complete. Contract/native-export/render regression, not a successful real owner revision. |
| `e92124b5ec57aabee49bf6d1dff4db9625fc3968` regression | `/root/ppt-prepare-task-e92124b5ec57-7731094098-checks/summary.private.json` | All 32 stages and 1,308 tests passed; cleanup complete. Includes the final frontend type repair. |
| `e65a71f56d28ba7d1be665f8d7e14be903a7935e` regression | `/root/ppt-prepare-task-e65a71f56d28-34a40e059d-checks/summary.private.json` | All 32 stages and 1,308 tests passed after the real `revise` naming correction. |
| Authoring Worker build at `dd50e900b30a2a468bdcde84062093370725a93b` | `/root/ppt-authoring-worker-dd50e900b30a-build-20261103T163121Z-166b956e/receipt.json` | `agent-service-pi:latest` digest `sha256:c1f6300fa0737459b738dd700952b98f0b76e0fac0493280e97f99147e78babe`; subsequently activated as recorded below. |
| Site build r1 | Retained original failed build evidence | TypeScript compilation failed before a successful site image. Preserve that failure separately. |
| Site build r2 | `/root/ppt-owner-history-site-build-20261104-r2/receipt.json` | Build passed after `e92124b5ec57aabee49bf6d1dff4db9625fc3968`; subsequently rolled out and exercised through the owner website. |
| Site build r3 | `/root/ppt-owner-history-site-build-20261104-r3/receipt.json` | Build passed at `e65a71f56d28ba7d1be665f8d7e14be903a7935e`; site digest `sha256:f4b99fb5b2b9a578c1a9355401b5096e3b8e5a8b0e17641e5f0a6dab1af48a00`. Its rollout subsequently passed as recorded below. |
| Current full catalog and candidate Worker reader | `/root/ppt-owner-render-worker-preflight-dd50e90-r1/catalog-compatibility.private.log` | Full current catalog of 95 retained entries and the candidate image probe passed. Compatibility is not activation or owner-deck acceptance. |

Images use `latest`; release identity belongs in exact digests, Git/source labels
and deployment annotations. No commit/version image tags are introduced.
Private receipt paths identify retained server evidence; they are not public
downloads or credentials.

## Runtime prerequisite checks and Worker activation

All five isolated runtime-profile prerequisites passed. The final receipt
confirms owned resources cleaned, UID scopes empty and dispatcher restored:
`/root/agent-service-deploy-20261026/ppt-owner-render-runtime-dd50e90-r1/operator-finished.json`.
These are runtime prerequisites, not a successful ten-page owner revision.
The full-95-catalog compatibility script's server and local SHA256 both equal
`cd3c5ae7f4f8393b0bb97b230def444514487372667d3b7b337017c54f08b0d6`.

The first Worker activation plan was refused by the production active-task
guard with one active task. It did not pause or interrupt that task. The
operator waited for the task to finish naturally, then planned and applied the
activation successfully. Final runtime evidence:
`/root/agent-service-deploy-20261026/ppt-owner-render-publication-dd50e90-r1/final-runtime.json`.

The new catalog configuration has identity `482971193a10caa8f7fd`. Five new PPT
bindings bring the retained binding count from 95 to 100. Existing shared
control image/source lineage is preserved, including immutable control digest
`sha256:7b8e4c4b8b6a620adb9842dbfa311b49de9be684e145e9892e4520e2308a3717`.
The UID reservation with base `47185920` and size `65536` is permanent. Empty
post-probe UID scopes do not authorize deleting that reservation or reusing its
range.

## Site r2/r3 rollouts and completed owner history check

The history site at `e92124b5ec57aabee49bf6d1dff4db9625fc3968` was successfully
released. The immutable running site identity and release checks are retained in
`/root/ppt-owner-history-site-plan-20261104-r2/apply-eeb32f1c66764eb78558afd0026e0bbd/release-receipt.json`.

The authenticated owner website showed 20 task records for the DSH project.
Selecting old generation Job `43133abb-2e94-4560-860a-2c1504f8ed42` exposed its
`QUALITY_GATE_FAILED` category and original recorded time, with an explicit
message that no detailed diagnosis was available for display. No detailed report
for that old task was obtained. Selecting revision Job
`b7fe65d5-ab02-4dc2-86fc-89b8917dae35` exposed the existing Office-related
`execution_needs_input` questions. Refresh and full reload preserved the
original recorded times and allowed reselection. Switching to the separate
“dsh代码学习” project did not show the previous project's records. These were
read-only observations; no new work was created.

Real owner evidence items 10–14 are retained under the outer workspace directory
`.research-temp/ppt-owner-c5-c10-20261103-r1/`. The history functionality has
passed this actual signed-in check. Its `revise` label defect prompted the small
follow-up fix and r3 build above.

The r3 site rollout then passed. Final release receipt:
`/root/ppt-owner-history-site-plan-20261104-r3/apply-d128445df27244c5b4488631f5d3e2af/release-receipt.json`.
The real owner UI now displays `revise` as “按反馈修改”. Old failed tasks,
refresh/full-reload continuity and cross-project isolation have all been
verified after the naming repair. C10 and its matching parent R7 scope are
checked in OpenSpec. This completes the scoped history/read-only owner gate,
not C5/TL7 or any other parent requirement.

## C5 revision stopped at a model-response failure

Through the normal authenticated website flow, the owner submitted revision Job
`1cb5f9d1-3918-4395-bc5e-6f9ce368cf0c` at `2026-11-03 17:10:24 UTC`, using
explicit baseline `7ee06cd9-ea01-40a1-8048-532f6ef71434`. Its actual Worker pins
select source `dd50e900b30a2a468bdcde84062093370725a93b` and immutable image
`sha256:c1f6300fa0737459b738dd700952b98f0b76e0fac0493280e97f99147e78babe`.
Refresh and full reload returned the same job and original submission time;
they did not create another revision.

An earlier running checkpoint remains retained at
`/root/ppt-owner-revision-observe-1cb5f9d-r2.json`, with recorded cursor values
`197` and `195`. Those were progress observations, not completion or a
quality-pass claim. The additional real owner evidence items 15–20 are retained
under `.research-temp/ppt-owner-c5-c10-20261103-r1/` in the outer workspace.

The task subsequently failed as business `MODEL_RESPONSE_FAILED`; its Agent
result was `model_error`. Physical stop is proven, the Worker Pod is retired
and the outbox is mapped and acknowledged. The terminal observation records
cursor values `418` and `416`. The project still has two Versions; this task
created no new Version or downloadable revision. The failed result remains
historical evidence.

Read-only model-ledger evidence:
`/root/ppt-owner-retained-model-observe-20261104-r1.json`.
For actual attempt `3a6f1f8a-3ec1-49f2-aba0-02c710b8b105`, 49 response records
are retained. The first 48 contain `response.completed`. The last is an HTTP
200, fully retained stream with `response.created`/`response.in_progress` then
an `error` event carrying the gateway's fixed `upstream_error` category and
fixed failure message; it has no `response.completed`. A complete transport
record and HTTP 200 do not establish a completed model response.

Read-only gateway evidence:
`/root/ppt-owner-gateway-failure-observe-20261104-r1.json`.
The bounded `17:26:20–17:27:30 UTC` window contains one matching failure
candidate for caller `agent-execution`, alias `llm-trusted`, at
`2026-11-03T17:26:33.159Z`. Its fixed diagnostic fields are
`failure_category=provider_error`, `upstream_event=response.failed`,
`provider_error_code=server_error` and `upstream_http_status=200`;
the provider error type remains unknown. The gateway log has no request ID,
so this is a limited time/caller/alias correlation, not an exact request-ID
join. These facts do not establish a quota, context-length or transient cause.

## One bounded follow-up owner retry

After checking physical stop and preservation of the existing Versions, the
normal authenticated owner UI submitted one bounded follow-up revision Job
`966dd364-6b37-4125-8d30-53d6edacc769` at `2026-11-03 17:36:28 UTC`, with
original deadline `2026-11-03 18:06:28 UTC`. It uses the same seven feedback
items and explicit baseline `7ee06cd9-ea01-40a1-8048-532f6ef71434`.
No prompt, model, retry rule or quality gate was changed to obtain this retry.
There is no automatic resubmission loop or acceptance bypass.

Owner evidence item 22 records the failure, 23 the state before retry and 24
the new task, under `.research-temp/ppt-owner-c5-c10-20261103-r1/`.
The new result is pending. C5/TL7 remain unchecked: accepted submission, correct
Worker pins, refresh continuity and a classified provider error do not establish
a successful ten-page revision or output inspection. C10/R7 remain complete
within their separately evidenced history scope. Prior failed
build/activation/owner evidence is retained; no earlier failure is rewritten,
discarded or counted as successful domain acceptance.

## Follow-up evidence at the earlier checkpoint (superseded below)

1. Follow the bounded owner retry Job
   `966dd364-6b37-4125-8d30-53d6edacc769` to its actual terminal result. Preserve
   its original deadline and any concrete failure diagnosis; do not duplicate
   or interrupt this revision or infer another retry authorization. Pending.
2. On success record the new Version and parent binding, download SHA256/size,
   all ten exact final-PPTX-derived page inspections and baseline hash recheck.
   Retain draft/review state and do not post human approval or formal publication
   on the owner's behalf. Only then reassess C5/TL7 against their exact scopes;
   C10/R7's completed history acceptance remains separately evidenced above.
   Pending.

## 2026-11-04: 1c39b80c intermediate review checkpoint (superseded below)

The exact earlier retry reached `succeeded` and physically stopped, producing
review Version `1c39b80c-287f-4186-a8de-7424da2a404d` for Job
`966dd364-6b37-4125-8d30-53d6edacc769`. Its explicit parent is the retained
`7ee06cd9-ea01-40a1-8048-532f6ef71434`. Terminal operational evidence is
`/root/ppt-owner-revision-observe-966dd364-r7.json`. The new review does not
change the old baseline or constitute formal publication.

### Real owner channels and separate administrative evidence

Through the normal authenticated owner UI, the exact new PPTX was downloaded:
55,029 bytes, SHA256
`9a1f343a233b3bc22014803bc4e5a8b235327a7173aeb915e19a23b83aca497e`.
The original baseline was also actually downloaded again after the task was
terminal: 53,781 bytes, SHA256
`fc620aa89f92e85486cfbd05fd0a3751247c315b14870af24dede769c7522d79`,
unchanged from the retained original. This successful re-download is distinct
from the previous round's disabled-button/timeout attempt, which did not
produce a successful baseline hash recheck.

The owner acceptance API supplied current programmatic receipt SHA256
`bea0601b1870945149c4022055f3394b8c66ea0d3149f3debe7f5617f78337eb`.
All ten final-PPTX-derived images were downloaded through its protected
`acceptance/rendered-slides/{n}/preview?receipt_sha256=...` routes, and each
downloaded hash matched the corresponding retained final-render descriptor.
These are exact final-PPTX Office-rendered pages, not the ordinary source-SVG
preview route. Two agents actually viewed all ten existing PNGs and read all
ten server-extracted speaker notes; root additionally viewed pages 3 and 6.
This is agent review, not a signed website human review.

Private local evidence under
`D:/github/armbianbegin/.research-temp/ppt-owner-c5-c10-20261103-r1/`:

- `revision-fingerprint.json` and `DSH-revision-1c39b80c.pptx`.
- `baseline-fingerprint-rechecked.json` and
  `DSH-baseline-7ee06cd9-rechecked.pptx`.
- `owner-final-image-downloads.json` and `final-owner-pages-1c39b80c/01.png`
  through `10.png`.
- `final-inspection-r3.json` and `visual-content-review-1c39b80c.md`.

The separately executed read-only administrative r3 inspector verified retained
lineage, runtime/source/receipt bindings, exact PPTX and ten PNG hashes, measured
text layout, baseline bytes, native structure and notes. Server output is
`/root/ppt-owner-final-advisory-20261104-r3/inspection.json`, SHA256
`c055a195faf6c648664c5e10de1a939f432ddb2796e25f10e2a0f4b68d388916`,
with `transfer-summary.json` in the same directory. It explicitly reports
`advisory_only=true`, `owner_api_evidence=false` and
`full_programmatic_acceptance=false`. It is not an owner API response or an
issued validator receipt. r1/r2 preparations remain unchanged; r3 was a new
read-only evidence tool for this exact partial-acceptance result. No production
quality gate was reduced.

Additional retained server observations are
`/root/ppt-owner-receipt-observe-20261104-r1.json` and
`/root/ppt-owner-source-warnings-observe-20261104-r1.json`. The actual immutable
business receipt remains at
`/var/lib/ppt-service/state/projects/c9d480b5-be18-46ed-839b-e65a9bd57eda/versions/1c39b80c-287f-4186-a8de-7424da2a404d/acceptance-evidence/bd8f1a92-978e-4434-9cc7-ce518a66bc50/receipt.json`.
Its exact hash is the owner-observed `bea0601b...` value above. No owner identity
or browser credential was manufactured for administrative extraction, and no
business database row was rewritten.

### Actual visual/content result and remaining quality warnings

The delivered file has 10 pages, 244 native shapes including 164 text shapes,
10 nonempty speaker notes, zero picture nodes, zero charts, zero semantic
connectors and zero connected connectors. There was no whole-slide raster
fallback. Visible arrows are native drawing content; these counts do not claim
OOXML semantic connector endpoints or actual desktop editing.

Exact final text layout is `passed`: 164/164 native text shapes matched and
checked, zero geometry-limited/unmatched shapes, 2,111 rendered glyphs,
`collision_glyph_pairs=0` and no layout advisories. The report's
`collision_shape_pairs=581` is not 581 detected text-collision failures.

Actual final-image review found no blocking clipping, broken short identifiers
or text collisions. The repaired page-3 shared `dsh-base` container and
independent `sdk-minimal` exception were preserved without arbitrary connecting
arrows. Six page-5 component labels remain complete and separate from Chinese
explanations; page 6 preserves the supplied execution ordering. Page 8 separates
durable log/model history from the UI stream and excludes failed/retried/
cancelled attempt records from model history. The README walkthrough and notes
are explicitly a teaching example, not a claimed execution; the config
whole-replacement example is illustrative, not a fabricated DSH default.
Page 9 retains the same-environment explanation without an automatic-migration
guarantee. Page 10 preserves the developer-preview boundary, original source
consultation date and source URLs. Full page-specific observations and image
hashes are retained in the local review record named above.

Nevertheless, the current receipt remains `needs_changes`, with four completed
gates: `independent_raster_regeneration_and_binding`,
`independent_delivery_audit`, `independent_native_ooxml_and_chart_data` and
`independent_final_pptx_rendering`. Required pending gates are still
`independent_export_build_contract` and `independent_postflight_and_delivery`.
The exact equivalence error is `export_equivalence_source_warnings`, not an
unexplained final-PPTX layout failure or merely an idle queued check.

The source quality report contains 10 pages, 8 passed, 2 warnings and 0 errors;
introduced=2, blocking=0, inherited=0 and source-import=0. Independent delivery
passed with no advisories. The two warning-bearing source files contain three
paragraph-like sibling-text runs:

| Page | Bound source location | Observed source-structure warning |
| --- | --- | --- |
| 4 | `04_config.svg`, `g id="patch-semantics"`, x=812, y=307..350 and y=458..500, two lines each | Paragraph-like lines are split across sibling `<text>` elements. The final image presents operation explanations and separate example steps legibly, but that visual observation does not waive the official source rule. |
| 6 | `06_turn_step.svg`, `g id="execution-chain"`, x=912, y=368..406, two lines | `agent/pre-step` and `prepareCall` are distinct mechanism labels and display correctly, but their source grouping still requires a precise semantic repair. |

The official warning instructs one actual prose paragraph to use direct text
plus direct `<tspan>` children with parent x and positive relative dy; genuinely
independent text frames should remain separate. The correction must express
that semantic distinction rather than hide the warning, move unrelated text
arbitrarily or weaken the checker. Bottom-level postflight source checks do not
complete the acceptance aggregate's clean source-quality gate while warnings
remain. No full export/desktop/human acceptance is inferred from successful
final rendering or an agent's visual judgement.

### One targeted normal-owner revision running at the earlier checkpoint

After preserving the downloaded review and all evidence above, the normal
owner UI submitted one bounded, targeted structural correction Job
`76fa036e-7f02-47c4-9f99-4c05d85dfe9d`, explicitly based on Version
`1c39b80c-287f-4186-a8de-7424da2a404d`. It was submitted at
`2026-11-04 10:26:36 CST` (`02:26:36 UTC`); its original phase deadline is
`2026-11-04 10:56:37 CST` (`02:56:37 UTC`). The deadline is not an estimated
finish time. The current observation is running, not a new accepted Version.

Mapped remote Job: `6d0dfb2e-986c-431f-9c6f-68525607c428`.
Read-only receipt: `/root/ppt-owner-revision-observe-76fa036e-r1.json`.
The actual Worker remains source `dd50e900b30a2a468bdcde84062093370725a93b`,
digest
`sha256:c1f6300fa0737459b738dd700952b98f0b76e0fac0493280e97f99147e78babe`.
No model, runtime image, retry rule or production acceptance gate was changed;
there is no automatic paid retry loop or administrator mutation of the retained
business artifacts. No application rebuild or rollout is required by this
documentation update.

Continue from this exact Job's terminal outcome; do not submit a duplicate.
If a successor Version is produced, check its explicit parent, current complete
programmatic results, exact owner download and all ten newly bound final-PPTX
images/notes, retaining both `7ee06cd9...` and `1c39b80c...`. Any remaining
warning or new failure requires its actual evidence, not a bypass or success
assumption. **C5/TL7 remain unchecked**, while C10/R7's separately completed
history scope stays complete. No website human signature, production PowerPoint
edit/save/reopen acceptance, formal publication, OpenSpec apply/archive or local
application parsing/testing/export/rendering was performed in this round.

## 2026-11-04: targeted correction completed with seven aggregate gates

Job `76fa036e-7f02-47c4-9f99-4c05d85dfe9d` reached `succeeded`, stage `review`,
with `stopped=true` and outbox acknowledgement `acked=true`. It produced actual
review Version `6f7c220f-01a6-4ab0-8703-180f091658d9`, explicitly parented by
`1c39b80c-287f-4186-a8de-7424da2a404d`. Its remote mapping remains
`6d0dfb2e-986c-431f-9c6f-68525607c428`, and the observed Worker remains the
previously recorded source `dd50e900...` / digest `c1f6300f...`. No duplicate
revision, model/runtime switch or production quality-gate change was needed.

### Exact delivered file and current owner acceptance result

The normal authenticated owner UI downloaded the exact new PPTX: 55,029 bytes,
SHA256 `5537e92f0a157604e8176d1a343bb86af0a27ea74882437640b97cc5ad3e4448`.
Local evidence is `structural-revision-fingerprint.json` and
`DSH-revision-6f7c220f.pptx` under the same private owner evidence directory
listed above. This file has the same byte length as its parent but a different
measured hash; it is not described as the same PPTX byte stream.

The real owner UI reports independent programmatic checks `passed`, with exact
receipt SHA256
`0ef5dcde635f19f040e48fadd5e1c7f109b2f4fd84879dcd2b4f9b33d8de4a6f`,
issued `2026-11-04T02:40:02Z` (`2026-11-04 10:40:02 CST`). All seven aggregate
gates are completed, with `required_pending_gates=[]`:

- `independent_raster_regeneration_and_binding`.
- `independent_delivery_audit`.
- `independent_native_ooxml_and_chart_data`.
- `independent_postflight_source_and_quality`.
- `independent_final_pptx_rendering`.
- `independent_export_build_contract`.
- `independent_postflight_and_delivery`.

Real UI evidence is `31-final-seven-gates-passed.txt` / `.png` and the complete
seven-item view in `32-final-programmatic-result.png`. The read-only
server observation
`/root/ppt-owner-receipt-observe-6f7c220f-20261104-r1.json` confirms source
quality 10/10 passed, zero warnings and zero errors, and equivalence `passed`.
The prior page-4/page-6 source warnings were repaired rather than ignored or
waived. Only `04_config.svg` and `06_turn_step.svg` source hashes changed; the
other eight source hashes stayed the same.

The lower-level postflight subreport still has `status=pending`, and
`full_exporter_postflight_verified=false` remains an explicit original field.
Those fields were not rewritten to obtain this result. The new aggregate
acceptance receipt independently completes the seven named gates; it does not
claim original-export provenance, an expanded full-exporter attestation,
desktop PowerPoint acceptance or human approval.

### Strict read-only r4 corroboration and unchanged reviewed content

The strict server r4 inspection completed at
`/root/ppt-owner-final-advisory-20261104-r4/inspection.json`, SHA256
`8c2f2a30c930f161ba4eadbefeb93d9e75af6ff66cce89d27010478e7516df87`.
The local retained report is `final-inspection-r4.json`. It verifies this new
Version, parent, exact final PPTX, current passing receipt and retained final
PNG evidence. Administrative extraction remains read-only advisory evidence,
separate from the normal owner API channel and the actual validator issuer.

All ten new images were downloaded through the owner API's final-render routes
bound to the new `0ef5dcde...` receipt, saved in
`final-owner-pages-6f7c220f/01.png` through `10.png`, and checked against that
receipt's page hashes. `owner-final-image-downloads-6f7c220f.json` retains the
channel and exact bindings. Each new PNG is also byte-for-byte identical to the
corresponding `1c39b80c` final image that the two reviewers actually viewed.
All ten server-extracted native page texts and speaker notes are unchanged.
The exact comparison is saved in `structural-final-comparison.json`.

Accordingly the previously recorded actual ten-page visual/content review
continues to describe these identical final images and notes; no unobserved
rendering or changed content is substituted for that review. The final deck
retains 10 pages, 244 native shapes including 164 text shapes, 10 nonempty notes,
zero picture nodes and zero semantic connectors. Final text layout remains
`passed`, with 164/164 native text shapes matched and checked, zero unmatched/
geometry-limited shapes, zero glyph-collision pairs and no advisories. This
structural correction removes the official source warnings while preserving
the actually reviewed ten-page presentation and source-faithful explanations.

After terminal Job `76fa036e`, the parent `1c39b80c` was downloaded again through
the owner UI: 55,029 bytes, unchanged SHA256
`9a1f343a233b3bc22014803bc4e5a8b235327a7173aeb915e19a23b83aca497e`;
see `parent-1c39b80c-fingerprint-rechecked.json`. The earlier successful owner
re-download of original `7ee06cd9` remains unchanged at the `fc620aa8...` hash
recorded above. Both old Versions, all failed attempts and the `1c39b80c`
`needs_changes` receipt remain preserved; the newer pass does not relabel an
older failure or partial result.

### Completed scope and remaining owner gates

This completes the limited C5/TL7 normal-owner DSH revision scope: the authorized
source-faithful correction, exact immutable Version/parent, actual ten-page
final-image and note review with byte-identical successor verification, normal
owner download, current independent programmatic gates and preserved old
Versions. C10/R7's separately evidenced owner history scope remains complete.
Root handles the corresponding OpenSpec status and Git handoff separately.

R8's real human acceptance declaration, R9's actual PowerPoint open/edit/save/
reopen on this production Version and R10's explicit formal publication remain
pending. No website human/PowerPoint signature or publication authorization was
submitted, and the Version remains a review draft. Synthetic earlier desktop
evidence is not acceptance of this production file. There was no production
business-row rewrite, Casdoor database change, local application test/build/
PPTX parsing/export/rendering, quality-gate bypass or OpenSpec apply/archive.
