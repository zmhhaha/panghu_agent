# Generation follow-up — 2026-10-31

The preparation-capacity repair is retained. A read-only production check found
the two original failed jobs unchanged, the source asset parsed, confirmed plan
revision 2 available and no post-release version. After the owner signed in, one
normal website generation request used that existing confirmed plan. No failed
record, deadline or production business row was manually rewritten.

## Actual business execution

- Project: `c9d480b5-be18-46ed-839b-e65a9bd57eda`.
- New preparation: `951a7b07-55c1-4139-b106-95dbe0613c34`.
- Remote preparation: `70281a14-aab2-4ceb-a63c-16881697ad06`.
- Preparation succeeded through collection, independent validation and
  candidate-received acknowledgement at `2026-10-31T05:22:15Z`.
- The normal controller automatically enqueued generation:
  `3a16d1da-21ff-456c-9d0e-a087e84639e2`, remote
  `45dee125-8da3-4872-9e26-991d1402cd8f`.
- Generation retained the original immutable cutoff
  `2026-10-31T05:52:17.346348Z`, but failed before that deadline at
  `2026-10-31T05:38:36Z`. The authenticated Agent result is
  `failed/worker_output_invalid` with physical stop proven; its mapped business
  record remains the historical `execution_interrupted` error. No version was
  created by that run, and no retry had been submitted at that checkpoint.
- All 38 retained model responses completed normally. This failure concerns
  output collection/handoff, not a demonstrated provider failure. The worker's
  ephemeral directory and Pod were removed by normal terminal cleanup; neither
  a reconstructed file tree nor a usable PPTX is claimed from the model ledger.
- Read-only terminal metadata is retained at
  `/root/ppt-generation-followup-status-20261031.json`.

The earlier synthetic upstream failure was inspected read-only. Its retained
model ledger has five completed HTTP 200 calls followed by an HTTP 200 response
containing the fixed normalized upstream error. The original provider payload
is not retained; quota, timeout, context and image causes cannot be inferred.
This inspection submitted no new model request and changed no production row.

## Precise model error reporting

Application runtime source `625113c432627e132b5bcb90fda3b9b3d32c65c1` maps only
terminal `failed` Agent jobs with exact fixed codes `model_error` or
`model_http_transient` to `model_response_failed`. It does so after existing
binding and physical-stop checks. Cancellation, timeout and unknown-error
precedence, immutable deadlines, single submission, confirmations, checkpoints
and artifact/review/publication gates remain enforced. Public Chinese copy
describes the failed model request without guessing its provider cause.

The application commit was pushed to main and fetched into the independent
server Git checkout `/root/ppt-model-failure-20261031/panghu_agent`. All 185
focused Linux-server checks passed, with no skips or failures. Evidence:
`/root/ppt-prepare-task-625113c43262-cb4d575874-checks/summary.private.json`.
No local tests or builds ran.

Only site and controller were built and pushed with `latest`, then verified
against immutable registry manifests, image configuration and source labels:

| Component | Digest |
| --- | --- |
| `ppt-service-site:latest` | `sha256:4accd4cb7d5937851ff0335c426527e84968bebd424d1aa6b72c44ef0d1f3698` |
| `ppt-agent-controller:latest` | `sha256:c15a0b88d8067f2200195eb4c7ebbb0f6bb09a880e01ab1f4cef0c4cccbbefd5` |

Build receipt:
`/root/ppt-model-failure-625113c43262-build-20261031T051848Z-e81bf5dc/receipt.json`.
The new images were deployed after the business execution stopped, completing
at `2026-10-31T05:47:04Z`. Receipt:
`/root/ppt-model-failure-site-controller-625113c43262-20261031T054523Z-fad993c2/release-receipt.json`.
Both application image digests and source hashes matched the tested Git source;
the site/controller are ready. Public ping200/root302/me401/projects401 passed.
The independent PostgreSQL admission guard and deployment lock were released.
The first release attempt correctly refused a remaining job lease and made no
deployment mutation; the later attempt retained the same strict drain checks.
Agent/LLM/validator/catalog, profile revision v2, authentication, shared database
and production storage are outside this two-image release.

The release operator must hold the existing PostgreSQL admission advisory lock
73114001 in an independent data/PostgreSQL session across both app rollouts.
Read-only drain checks include queued/parsing/stopping sources, every remaining
parse lease, jobs, slots and outboxes. UID/resourceVersion checks and deployment
lock ownership remain required; no business migration or automatic rollback.
Six isolated PostgreSQL guard checks passed on the Linux server, including real
target-container restarts, competing admission, normal cleanup and orphan timeout.
They used a cached immutable PostgreSQL image, no network or production data,
and confirmed zero business writes and no remaining owned containers or locks.
Evidence: `/root/ppt-model-admission-guard-checks-3c3a1ecee4cc/summary.json`.
The first fixture launch stopped during the official image's temporary init
server shutdown; its owned container was cleaned. The corrected readiness check
requires the final PostgreSQL PID 1 and successful SQL before testing the guard.

Full generation, review/revision/download and owner PowerPoint acceptance remain
pending. Do not archive the OpenSpec change or claim complete domain acceptance.

## Follow-up fixes and server verification

Application `b821cb5346cb28c37786cfa2f435f3b36fa6214e` adds bounded plain-text
questions only for independently validated, stopped `needs_input` results.
The job remains failed with `execution_needs_input`; API/job history and the
project's latest-job projection use the same attempt/fence and business binding.
Reloading or opening the guidance does not submit a model task. Existing
`worker_output_invalid` records cannot enter that path and remain unchanged.
All 218 focused Linux checks passed without failures or skips; evidence:
`/root/ppt-prepare-task-b821cb5346cb-dd7be8dd09-checks/summary.private.json`.

The actual site build then refused a missing import: the new helper was absent
from the explicit package roster. No image was published or deployed by that
failed build. Both site packagers now include it, packaged-image verification
requires its installed path/hash entry, and isolated import tests exercise the
actual packaged source rather than the repository Python path.

Read-only AST inspection of the retained final tool request found a
`needs_input` manifest literal with nonempty questions, missing `artifacts` and
`provenance`, and no subsequent mutations in that heredoc. This is request
evidence; it does not reconstruct tool results or the removed worker files.
The pinned PPT Master 6.7.2 workflow (revision `c350bc31`) permits continued
execution through finalization/export after the selected plan is confirmed.
Its live-preview step explicitly does not wait for another user confirmation;
the main agent may inspect actual PNGs sequentially when delegation is absent.

The live Pi profile instructions now explain that execution scope, retain real
approval/tool/failed-gate/needs-human blockers, and supply a complete typed
`needs_input` example with the real checkpoint project root and measured-file
roster requirements. Actual PNG review, independent checks, later website review
and owner PowerPoint acceptance remain required. Collection rules are unchanged.
Activating these instructions requires a new Pi worker image and exact runtime
bindings; a site/controller release alone cannot activate them.

The application runtime source at this release checkpoint was
`cbc649149c5c79b623bff1dc69bfe17a046c263c`. All 248 checks in 11 focused
Linux stages passed with zero failures, errors or skips, including both site
packagers, the controller source context and nonempty blocked checkpoints.
Receipt:
`/root/ppt-prepare-task-cbc649149c5c-858bbfbce3-checks/summary.private.json`.
The actual site/controller build also verified the installed helper and frontend
production build; no local tests, builds or development preview were used.

Site/controller release completed at `2026-10-31T06:18:59Z`:

| Component | Digest |
| --- | --- |
| `ppt-service-site:latest` | `sha256:3ff44f2122072d98f9c0a59b07eb8124345381b7d101e106dff8d0273cc0c25c` |
| `ppt-agent-controller:latest` | `sha256:394413b4e20864fad2106856cf5a0e69628b2991b67079e71b9ff4289ff65950` |

Build receipt:
`/root/ppt-model-failure-cbc649149c5c-build-20261031T061620Z-9ab9b9bf/receipt.json`.
Release receipt:
`/root/ppt-model-failure-site-controller-cbc649149c5c-20261031T061718Z-a9a5adcb/release-receipt.json`.
Exact running digests/source checks and readiness passed; public ping200/root302/
me401/projects401 passed. Both release locks were released. This release made
no business database write or model submission and preserved the shared Agent,
LLM, validator, authentication and production storage. At this release checkpoint,
the signed-in browser showed the unchanged failed generation and no review version.

## Pi-worker build, locking and runtime activation

Pi-worker activation followed as a separate operation. The worker was built and
pushed with `latest` from application source
`cbc649149c5c79b623bff1dc69bfe17a046c263c`; its immutable manifest is
`sha256:51b68b3ce583e7e13df0a52ce71d9002b23a3d2f79569d532c0452d226370a2a`.
Worker build receipt:
`/root/ppt-model-worker-cbc649149c5c-build-20261031T063142Z-78ccac93/receipt.json`.
This is separate from the site/controller build. Retained control and validator
images were not rebuilt by this worker release.

The locking review found that legacy preflight and publication UID-ledger writers
used different locks. The supported operator wrappers now share the lock order
`workflow -> maintenance -> uid`, including supported legacy entry points.
Historical bare native implementations were not changed and were not called
directly. Eight operator checks and six real Linux multiprocess reservation
checks passed with zero failures or skips. The multiprocess receipt is
`/root/ppt-worker-uid-lock-checks-20261031-r1.json`. UID range start `46331072`,
size `65536`, remains permanently reserved; cleanup does not recycle it.

Five fresh fixed-echo route preflights passed. Their owned resources were cleaned
and dispatcher restoration was verified. These synthetic preflights establish
runtime routing and cleanup, not actual PPT generation or visual acceptance.
Evidence directory:
`/root/agent-service-deploy-20261026/ppt-handoff-runtime-cbc6491-r1`.

The runtime publication applied ConfigMap
`agent-service-config-c17650bb11d5d8b6380a`, and its explicit restoration step
completed successfully. All three control deployments were Ready with one
replica each. They retained the original live image
`sha256:b3356d27dbdc31c02030f7896314efac3d4d4eceff51eb0c575c219d2647db25`
and their original source annotations. The validator retained
`sha256:01039dbf8076756d1bc12e3a1a4b346b142d457662f5178b9c72cb50b0ac6bed`.
Only the five PPT routes' `binding_revision` values changed; unrelated routes,
the v2 contract, bundle pins and existing checkpoint compatibility were preserved.
Publication state and receipts:

- `/root/agent-service-deploy-20261026/ppt-handoff-publication-cbc6491-r1/publication/plan.json`
- `/root/agent-service-deploy-20261026/ppt-handoff-publication-cbc6491-r1/publication/publication-result.json`
- `/root/agent-service-deploy-20261026/ppt-handoff-publication-cbc6491-r1/restored.json`
- `/root/agent-service-deploy-20261026/ppt-handoff-publication-cbc6491-r1/final-runtime.json`

## New business execution and latest terminal result

One normal signed-in website generation request used the existing confirmed
plan after runtime activation. It created preparation job
`68927583-b813-4028-a3d1-8659c29aaf5a` at `2026-10-31T08:02:41Z`, with its
original deadline `2026-10-31T08:32:42Z`. Its remote preparation was
`b068d5ff-afdf-48f0-9c06-e2438092bdb9`. During preparation, the active worker
Pod was verified to use
`sha256:51b68b3ce583e7e13df0a52ce71d9002b23a3d2f79569d532c0452d226370a2a`.
Preparation succeeded through independent validation, physical-stop proof and
candidate-received acknowledgement. Historical failed jobs and their deadlines
remain unchanged.

At `2026-10-31T08:09:49.979352Z`, the normal controller automatically enqueued
generation with these bindings:

- Business job: `0ecd03b2-898c-4e52-a742-1122da3038a8`.
- Remote job: `85441f85-ac7f-471d-8bfc-b61c9ab7c483`.
- Attempt: `70582ccf-8813-4c47-b983-db29512d16b9`.
- Fencing token: `60`.
- Original immutable deadline: `2026-10-31T08:39:52.134469Z`.

The subsequent read-only status and website check found that generation had
terminated at `2026-10-31T08:12:14.897233Z` as business
`failed/model_response_failed`. Its remote result was `failed/model_error`,
with physical stop proven. The mapped outbox was
acknowledged (`acked=true`, cursor `89`), and the business job was stopped.
The project remained at `stage=draft`, with `active_job=null` and
`latest_version=null`. No version or PPTX was produced. Read-only terminal
evidence is retained at `/root/ppt-post-handoff-status-20261031.json`.

The persistent model-ledger summary records seven `response.completed` events
followed by an eighth normalized `upstream_error`. Evidence:
`/root/ppt-generate-70582ccf-diagnostics-20261031/model-safe-summary.json`.
The independent gateway summary records the failed request: an HTTP 200 stream
containing `response.failed`, with `failure_category=provider_error`. Evidence:
`/root/ppt-generate-70582ccf-diagnostics-20261031/gateway-safe-summary.json`.
The original provider code and type remain unknown; whether the error was
transient and whether quota or context caused it cannot be inferred.

Preparation and runtime activation are verified progress, but this run did not
complete PPT generation. There is no successful preview version,
review/revision/download or owner PowerPoint acceptance evidence from this run.
Full domain acceptance remains pending; these build, operator and publication
results do not justify marking the OpenSpec change fully accepted, applied or
archived.

## One bounded follow-up request

After the failed attempt had physical-stop proof, acknowledgement and no active
project job, one further normal website request was submitted within the
authorized follow-up. This is a single bounded verification request, with no
retry loop, model or quality-gate changes, or internal SQL submission. The normal
UI starts preparation again rather than reusing the previous prepared result:

- Preparation: `8c973932-ffa6-4609-b537-df2cf6c68e3c`.
- Remote preparation: `005dc544-e7d2-4e4c-95e9-b1f1232c304c`.
- Attempt: `5dd9c357-605d-4153-a242-58a17109bc72`.
- Fencing token: `61`.
- Created: `2026-10-31T08:19:59.064015Z`.
- Original immutable deadline: `2026-10-31T08:49:59.428952Z`.
- Project revision: `15`.

This preparation succeeded, and the normal controller automatically enqueued
generation with these bindings:

- Business job: `fd039d97-33ec-4824-a449-f683d86f4b8b`.
- Remote job: `0236e8d6-ec8e-4f9b-ab49-23df0b847bd7`.
- Attempt: `923e5194-12fc-46c6-a5da-285d1422e177`.
- Fencing token: `62`.
- Created: `2026-10-31T08:25:39.476521Z`.
- Original immutable deadline: `2026-10-31T08:55:41.597768Z`.

Generation terminated at `2026-10-31T08:42:11.644202Z` as business
`failed/execution_needs_input`. The retained inventory contains 29 files,
including all 10 SVG slides. The official `visual_review.py` could not render
the 10 pages against the trusted `http://127.0.0.1:8765` preview:
`BrowserContext.new_page` raised `TargetClosedError`. Repeated page errors can
follow the first shared-browser failure; they do not establish 10 independent
browser crashes. This was a rendering prerequisite blocker, with no preview
version or PPTX delivered.

Both live-log observers had exited after fsnotify reported `too many open
files`. A bounded read-only copy of host CRI logs was then taken after matching
the Kubernetes Pod UID and container identity. The observer error alone does
not establish exhaustion under the distinct Agent UID. Diagnosis proceeded
without another unchanged business retry.

## Render process-budget diagnosis and repair

The resource failure was reproduced on the Linux server without a model or
business data. Linux `RLIMIT_NPROC` includes threads: the original limit of 64
was shared by Pi's Node process, Python, the Playwright driver and Chromium.
An isolated comparison used the same immutable Pi image, the existing root
launcher and dynamic-UID drop, NNP, cleared Agent capabilities, read-only root
and `agent-capsule-inputs` AppArmor. Each case kept a Node parent alive while
Playwright created pages and took screenshots of 10 synthetic SVGs:

| Agent process/thread limit | Sampled thread peak | Actual PNGs | Result |
| --- | --- | --- | --- |
| 64 | 64 | 0 / 10 | `pthread_create`, `EAGAIN`, `TargetClosed` |
| 128 | 79 | 10 / 10 | Completed |
| 256 | 83 | 10 / 10 | Completed |

There was no OOM evidence. All three cases measured the expected UID/GID,
NPROC, NNP and empty Agent capabilities, stopped their fixture UIDs and removed
their owned containers. Fresh fixture UIDs were outside the permanent production
reservations; no production UID ledger was changed. Receipt:
`/root/ppt-chromium-limit-probe-20261031-r1/summary.json`.
Docker's default seccomp is not asserted identical to Kubernetes RuntimeDefault,
and the synthetic comparison does not constitute business acceptance.

Application commit `ca1237c42f6b3f7fa5e437a40ae1232cb844f859` was committed and
pushed to main. The shared production/candidate settings helper now sets a hard
budget of 128 only for the exact trusted `ppt.generate.v1` and `ppt.revise.v1`
profiles. Preparation, planning, research and workspace execution retain the
default 64. Global defaults, profile revisions, schemas, immutable deadlines,
UID isolation, capabilities, NNP, collection and quality/publication gates are
unchanged. All 378 focused Linux-server checks passed. Receipt:
`/root/ppt-prepare-task-ca1237c42f6b-fb4f7bae48-checks/summary.private.json`.
No local tests, compilation, builds or development previews were used.

The repaired Pi worker was built and pushed with `latest`; its immutable image
is `arm-cluster-master:5000/agent-service-pi:latest@sha256:ee9721305b4924fbc9b397103198aa2acf8720e4aee2a204b529ca44b5f6e134`.
Build receipt:
`/root/ppt-model-worker-ca1237c42f6b-build-20261031T092743Z-2a07a688/receipt.json`.

A separate official-render probe used the real `TrustedPreview.start()` with its
PID/project/SVG-directory health checks, a distinct preview UID and the actual
fixed `visual_review.py` CLI. With Agent NPROC 128, it produced all 10 expected
PNGs in `deck/.preview`, returned CLI exit 0 and reached a sampled Agent thread
peak of 80. Both measured identities were valid; both UIDs stopped, preview
locks were cleaned and the owned container was removed. There was no timeout
or OOM. Receipt:
`/root/ppt-chromium-official-probe-20261031-r1/summary.json`.
This proves synthetic official rendering with the production UID separation;
it does not supply a real presentation, visual review or accepted version.

No real PPTX has been accepted at this diagnostic checkpoint. Full generation,
preview/review, revision, download and owner PowerPoint acceptance remain
pending; the OpenSpec change must not be marked fully accepted, applied or
archived on these diagnostic results alone.

## Repaired worker runtime release

The reviewed operator bundle is
`/root/ppt-render-publication-operators-20261031-r1`. Its narrow `CHECK_STAGES`
allowlist was extended to the exact 13 focused stages while retaining strict
release validation. Eight offline operator checks and six Linux multiprocess
lock checks passed. New lock receipt:
`/root/ppt-render-uid-lock-checks-20261031-r1.json`.
The official-render probe also fully decoded all 10 PNGs with Pillow; each was
1280 by 720 pixels. Decode receipt:
`/root/ppt-chromium-official-probe-20261031-r1/png-decode-verification.json`.

All five fresh runtime preflight stages passed, followed by owned-resource
cleanup and dispatcher restoration. Evidence:
`/root/agent-service-deploy-20261026/ppt-render-runtime-ca1237c-r1`.
These preflights and synthetic PNG checks do not replace actual PPT generation
or independent business acceptance.

The worker-only runtime publication activated the repaired `ee972130` image
through ConfigMap `agent-service-config-4fd05fd61dc263932d41`, followed by the
explicit restoration step. A fresh permanent UID reservation starts at
`46396608` with count `65536`; earlier permanent ranges remain reserved.
No receiver override parameters were supplied. The site/controller deployment
specifications, all three shared control images and source annotations, and
the validator were retained. All retained deployments had one ready replica.
Only the five PPT route `binding_revision` values changed; the v2 contracts,
bundle pins, existing checkpoint compatibility and unrelated routes remained
unchanged. Publication evidence:

- `/root/agent-service-deploy-20261026/ppt-render-publication-ca1237c-r1/plan-review.json`
- `/root/agent-service-deploy-20261026/ppt-render-publication-ca1237c-r1/publication/plan.json`
- `/root/agent-service-deploy-20261026/ppt-render-publication-ca1237c-r1/publication/publication-result.json`
- `/root/agent-service-deploy-20261026/ppt-render-publication-ca1237c-r1/restored.json`
- `/root/agent-service-deploy-20261026/ppt-render-publication-ca1237c-r1/final-runtime.json`

## Business verification after the render repair

One normal signed-in website request used confirmed plan revision 2 after the
worker release, creating project revision 16 and a new preparation:

- Business job: `e3d12632-52dd-465f-976f-8d4e52954c92`.
- Remote job: `c6a271c1-802a-4713-b389-9b014a5e818b`.
- Attempt: `86db595f-4507-46bd-b9fb-d539b13d61ef`.
- Fencing token: `63`.
- Created: `2026-10-31T12:02:29.279042Z`.
- Original immutable cutoff: `2026-10-31T12:32:30.079771Z`.

Preparation succeeded through independent validation, physical-stop proof and
acknowledgement. The controller automatically started generation:

- Business job: `17607ed5-11ab-4c9d-bae2-258f9c831533`.
- Remote job: `b661bb3b-6e50-4c6a-aad2-897a6152ba65`.
- Attempt: `22c347f1-65e1-4bb9-a8d7-858b4aa62d8a`.
- Fencing token: `64`.
- Created: `2026-10-31T12:12:32.564554Z`.
- Original immutable cutoff: `2026-10-31T12:42:34.691085Z`.

The earlier running checkpoint at
`/root/ppt-post-render-repair-status-20261031.json` is superseded by the subsequent
read-only terminal verification. Generation ended as remote
`failed/worker_output_invalid`, mapped to business `failed/execution_interrupted`.
Physical writer stop was proven and the corresponding outbox was acknowledged
at cursor `501`. No Version, website preview or accepted PPTX was created.

At `2026-10-31T12:39:30Z`, an intermediate directory inventory contained 136
files, including 31 SVGs, 10 PNGs and one PPTX. These were physical intermediate
files in the execution workspace. They were not a collected, validated or
published presentation, and the inventory does not establish the final handoff
roster after subsequent writes or cleanup.

The final retained model-ledger `write` request supplied a manifest with 89
artifact entries and 10 slide entries. That requested manifest has two confirmed
contract errors:

- `project_root` was `project/dsh_architecture_ppt169_20261030`, repeating the
  `project/` prefix. The actual child under `workspace/project/` was
  `dsh_architecture_ppt169_20261030`; that child path is the required value.
- The PPTX artifact role was `candidate_pptx`. The collector requires the literal
  role `pptx`; `candidate` belongs in the manifest-level status.

This is evidence of the requested write payload, not independent proof of the
final bytes persisted on disk. The first actual host collector exception was
not durably retained, so the evidence cannot identify which check failed first
or establish a more specific original host rejection code. The earlier
intermediate inventory and final requested manifest must not be treated as one
simultaneous complete file roster. No unchanged paid retry or historical failure
rewrite is part of this diagnosis.

## Read-only handoff preflight repair

Application commits `00eb92a` and `4162b27` are on `main`. They extract the
collector's existing pure read/contract validation into shared logic and add an
advisory preflight that the Agent runs against its actual output before ending.
The shared checks cover the phase and typed manifest fields, actual project
root, complete nonempty file roster, measured hashes and sizes, quality report
references, slide references, exact PPTX role, restored checkpoint binding and
the approved specification hashes required by the stage. Failed directory scans
now report the fixed `ppt_artifact_unreadable` code instead of silently omitting
an unreadable tree.
The success and blocked manifest examples preserve the supplied checkpoint's
child root and explicitly distinguish the `pptx` role from `candidate` status.

The domain host exports the actual validated task into a host-owned sibling of
the attempt workspace under `/work`, outside `project/` and the tree transferred
to the Agent. The parent and helper directory are traversable but cannot be
written by the Agent; `task.json` is read-only. The prompt supplies the exact
workspace and task paths to the image-owned CLI:

```text
/opt/agent-service/.venv/bin/python -I -B /opt/agent-service/agent_service/ppt_handoff.py --workspace <actual-workspace> --task-file <host-exported-task.json>
```

Isolated Python excludes the writable current directory and caller
`PYTHONPATH` from module discovery, and the entry point loads the fixed
image-owned service package. The CLI only prints bounded fixed diagnostics and
an advisory summary. It does not change files, repair a manifest, fabricate
`StopEvidence`, create a checkpoint/candidate/Version, perform visual review or
authorize business acceptance or publication. While the trusted preview is
active, only its two fixed ephemeral lock files are excluded from advisory
measurement; the manifest cannot declare those locks.

Formal collection still requires physical writer-stop proof first. After trusted
preview cleanup it independently re-reads the actual tree, ignores no files and
validates against the original trusted envelope task, rather than the exported
advisory snapshot or CLI result. Only that stopped host path creates the measured
checkpoint, includes its actual bytes in the final quota and proceeds through
the existing freeze, signing, independent validation and business gates. The
helper is removed by exact file/directory cleanup during the existing stopped
input-release lifecycle; failed release retains its reference.

Focused regression coverage has been added for both observed manifest errors,
hash/roster/quality/checkpoint/specification failures, empty blocked handoffs,
fixed preview-lock exclusions, protected task permissions and cleanup, isolated
CLI import behavior, real low-permission directory failures and the unchanged
stop gate. Linux-server verification of source
`4162b27ddb9714ddbd978bc6b21e03b42d4de6eb` completed all 13 focused stages:
454 checks passed, with zero skips, failures or errors. Receipt:
`/root/ppt-prepare-task-4162b27ddb97-3c48df4111-checks/summary.private.json`.
No local tests, compilation, builds or development previews were used.

The worker was built from the pushed Git source and its image push was verified.
It uses `latest` with immutable identity
`sha256:8fce72ba69b4d0acc5fa05fbe9c48c2fe850a33adc6acc44fb1123e8235d2028`.
Build receipt:
`/root/ppt-model-worker-4162b27ddb97-build-20261031T141151Z-d40bf3b8/receipt.json`.
The image-owned fixed CLI's `--help` entry ran successfully as UID `100000`, with
network disabled, a read-only root, empty capabilities and NNP. This verifies
the isolated CLI entry/import environment; it is not an actual presentation
handoff or domain acceptance result.

All five fresh fixed-echo runtime preflights passed, their owned resources were
cleaned and dispatcher restoration was verified. Evidence:
`/root/agent-service-deploy-20261026/ppt-handoff-preflight-4162b27-r1`.
These runtime probes do not constitute real generation, visual review or
business acceptance.

## Handoff preflight runtime publication completed

The publication plan passed review. Its only runtime changes were the
five PPT v2 route `binding_revision` values and the ConfigMap pointers of the
three shared control deployments. The exact existing images and source
annotations, validator, website and PPT controller were retained; no receiver
rebuild was included. Publication state directory:
`/root/agent-service-deploy-20261026/ppt-handoff-publication-4162b27-r1`.
The release uses a fresh permanent, non-reusable UID reservation starting at
`46462144`, with count `65536`; earlier reservations are not recycled.

Publication apply succeeded with ConfigMap
`agent-service-config-b74f66bcc4a421254a44`. The explicit restoration step also
succeeded, with `queued=0`. The final runtime receipt verifies one ready replica
for each of the three shared controls, the website and the PPT controller. Their
measured images and source annotations exactly match the fresh retained runtime
snapshot at `/root/ppt-handoff-4162b27-retained-live-runtime.json`.

For this publication, that fresh snapshot records the live image of all three
shared controls as
`agent-service-control:latest@sha256:10a383f64747242b83b0f226e062c3332254064f0a87bfc2bc92969939b1b9f0`.
This is the retention baseline for this apply; the earlier image identities in
this chronological log remain historical observations and are not rewritten.
Publication receipts:

- `/root/agent-service-deploy-20261026/ppt-handoff-publication-4162b27-r1/plan-review.json`
- `/root/agent-service-deploy-20261026/ppt-handoff-publication-4162b27-r1/publication/publication-result.json`
- `/root/agent-service-deploy-20261026/ppt-handoff-publication-4162b27-r1/restored.json`
- `/root/agent-service-deploy-20261026/ppt-handoff-publication-4162b27-r1/final-runtime.json`

## Post-release business verification blocked by authentication

One normal Generate click returned `UNAUTHENTICATED` with HTTP `401`. Following
the normal `/oauth2/start` route reached the Casdoor login form, with no valid
owner session available to continue. The login form was left open for the owner;
no request for interaction was made while the owner was away. No authentication
bypass, replacement owner identity, new paid Agent job, confirmation or Version
was created.

The read-only business status still shows project
`c9d480b5-be18-46ed-839b-e65a9bd57eda` at revision `16`, with latest job
`17607ed5-11ab-4c9d-bae2-258f9c831533` and no Version. Evidence:
`/root/ppt-post-handoff-preflight-status-20261031.json`.
Post-release business execution was not performed because authentication was
unavailable. The completed engineering release does not establish successful
PPTX delivery, preview/review, revision, download or owner PowerPoint acceptance.

- [x] Shared read-only handoff preflight, protected actual task export and
  isolated CLI implemented, preserving stopped host collection and later gates.
- [x] Git source push, Linux-server verification, worker image build/push,
  runtime preflights, publication, restoration and retained-runtime verification.
- [ ] Authenticated post-release business generation and complete domain
  acceptance, including actual PPTX and review/revision/download/owner checks.

The tested and running worker source remains
`4162b27ddb9714ddbd978bc6b21e03b42d4de6eb`. Follow-up commits that change only
this release record do not require an image rebuild or change the running
source/image identity. Full
business acceptance remains incomplete, and the OpenSpec change is not marked
fully accepted, applied or archived.
