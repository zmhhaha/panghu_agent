# Owner history and independent-render handoff — 2026-11-04

Scope: continuation of `complete-ppt-p0-p1-operational-acceptance`, especially
normal-owner C5/C10 and the ten-page text-layout revision. This records a failed
real owner attempt, the resulting fixes, server verification, Worker activation
and successful history-site rollouts through r3. C10/R7's real owner history
scope is complete. A C5 revision failed on a model response and one subsequent
bounded owner retry awaits its result. No successful C5/TL7 result, formal owner
acceptance or publication is claimed.

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

## Follow-up evidence to append before closeout

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
