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
  created and no paid retry was submitted.
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

## Follow-up fixes under server verification

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
