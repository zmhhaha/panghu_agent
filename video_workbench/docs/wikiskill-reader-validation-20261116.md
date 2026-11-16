# WikiSkill compilation: bounded canonical-input reader

The stopped compiler job `b5e91172-73c0-4198-b564-ede85ac4538d` never wrote
`output.json`. Its recorded model responses show two builtin `read` attempts
against a 147,374-byte single-line canonical manifest, followed by a truthful
statement that the builtin 50 KiB line limit prevented reading it. The
manifest has 502 record references, including 53 single-line records larger
than 50 KiB. This was an input-access failure, not a completed compilation
rejected for quality. This repair does not change that job or resubmit it.

## Confirmed live failure checkpoints

The original business publication operation was
`ca5cba98-470d-436a-9116-1f5adb252067`; its stopped v1 job was the
`b5e91172-73c0-4198-b564-ede85ac4538d` job above. It failed at input access
and produced no successful WikiSkill release. The original operation, input
bytes, failure and historical execution identity remain preserved.

The first v2 technical candidate, `candidate-wkc-v2r1`, subsequently failed
before admission. Its worker exited with status 1; the model proxy recorded
an upstream HTTP 422 and `model_upstream_rejected`. The v2 worker explicitly
requested 32,768 output tokens, while the shared llm-service request schema
still allowed only 8,192. Both the worker and proxy were actually observed
stopped: worker exit 1, proxy exit 0, with the proxy deletion timestamp
`2026-11-16T06:41:48Z`. The API and finalizer were preserved. A read-only
SQLite backup taken before stopping has SHA-256
`906f06caa83fdffcea7bca15c31dd8ec8205affe5375555f426b9347da187abc`;
the D-drive `wikiskill-compiler-v2-failed-candidate-stop.json` records this
checkpoint. Original request identity and failure state remain intact.
This failed candidate has not been technically admitted or activated;
stopping its processes is not a successful compilation, acceptance or release.

These are two different failures: the first exposed the builtin reader's
large-line limit; the second exposed the shared model gateway's explicit
output-budget boundary. Neither is evidence that v2 compilation or formal
video adoption has succeeded.

## New execution identity

`video.animation-wikiskill-compile.v2`, profile revision `1`, uses the new
`animation-wikiskill-v2` procedure and the exact tool set
`read_compilation_input`, `write`. The v1 profile, procedure, inputs and
historical bindings remain unchanged. Input/result document schemas remain
v1; v2 inherits the existing canonical-byte, complete-file-set, envelope,
writer-stop and result checks. The business gateway selects the new profile.

The fixed extension is image owned and SHA pinned. Its environment identity
binds extension bytes, exact tool scope, worker image, capabilities and runtime.
Candidate admission checks those same bytes and uses a new v2 acceptance suite.
The new combination needs fresh admission before activation; old acceptance
does not authorize the new image/profile/environment.

## Reader behavior

- Every call requires the frozen task's complete manifest SHA-256.
- Manifest pages include original header/index metadata, at most 128 requested
  records, an explicit next offset and a 32 KiB response-text bound.
- Initial record reads can be grouped in explicit batches of up to 32
  manifest-listed names. The response reports unreturned names and incomplete
  files explicitly. Large files continue through UTF-8 byte ranges.
- File access is limited to the fixed inputs root and exact sequential record
  paths in the verified manifest. Whole-file hashes and sizes are verified
  before returning any range. Links and changing file/directory identities
  are rejected. UTF-8 boundaries preserve exact source bytes.
- There is no shell, network, arbitrary path, code execution, input rewrite or
  unbounded dump. The original canonical JSON and its identity stay intact.

## Offline validation

The actual failed-job manifest and one 462,002-byte record were exported with
read-only SQL, then exercised locally with the real Node extension. All 502
manifest rows were preserved across five bounded pages. The large record
reassembled from 29 ranges to its exact original bytes and SHA-256. No model
request, database write, compilation retry or publication was performed.

Focused regressions cover large single-line input, manifest pagination,
explicit record batches, reply limits, UTF-8 reassembly, invalid offsets and
paths, missing records, changed hashes, hardlinks, cancellation, fixed extension
integrity, environment scope, production/candidate factory loading, runtime
token bounds, original collector behavior and preservation of v1 contracts.
Catalog/acceptance regressions cover all three roles (v1 compiler, progressive
planner, v2 compiler), source bundle capture and disabled additive preparation.

## Output-size limit

The 502 real record IDs contain 24,640 UTF-8 bytes. A minimal complete reference
list is 26,169 bytes / 13,456 o200k tokens. A hypothetical compact per-record
exclusion layout is 48,729 bytes / 17,970 tokens, or 19,987 tokens inside the
`write` call's escaped JSON arguments. These are size estimates, not authored
lessons or evidence that any source was read.

Only the v2 compiler therefore requests up to 32,768 output tokens, within the
existing model proxy ceiling; old profiles retain their 8,192 setting. The
procedure asks for compact focused Skills, brief concrete exclusions and no
unnecessary repeated long IDs. Roughly 12,781 tokens remain after the compact
exclusion argument example; this is sufficient headroom for a compact result,
not a guarantee that arbitrary expansive prose or all source text will fit.
No unbounded output budget or automatic retry is introduced.

## Shared model gateway dependency

Root-repository commit `84bbbb775e0c5da472a7fe8bf85ff427d314bfa8` has been pushed with the minimal llm-service
dependency repair. A shared `TRUSTED_MAX_OUTPUT_TOKENS = 32768` constant now
supplies all three necessary boundaries:

- `ResponsesRequest.max_output_tokens`, the public Responses request schema;
- `ChatRequest.max_tokens`, including the internal Responses-to-Chat request
  construction;
- the trusted tier's merged-parameter validation cap.

The request mapping remains explicit: public `max_output_tokens` becomes
internal `max_tokens`, then either Chat `max_tokens` or native Responses
`max_output_tokens` at the selected upstream. Raising only the public schema
would still fail at the internal Chat schema or trusted policy cap.

The guarded tier remains limited to 2,048. Existing worker profiles still
explicitly request 8,192; only the v2 compiler factory requests 32,768. No
route default or omitted-parameter behavior was changed. In particular, a
route without a configured budget still does not receive an injected budget,
and the fixture's existing DeepSeek trusted default remains 2,048.

The complete llm-service offline suite passed: 129 tests and 122 subtests,
with one existing Starlette deprecation warning. Integration regressions
exercise both public entrypoints through actual routing and mocked upstream
HTTP for both native Responses and Chat wire protocols. They verify unchanged
8,192 and 32,768 request values and complete mock output; rejection of 32,769
before upstream access; guarded rejection of 2,049; and preserved omitted
budgets and configured defaults. These tests make no live model request and
do not establish the deployed provider's output capability.

## Production-source reconciliation

The first shared-gateway rollout from `84bbbb` exposed a separate operator
error: that branch's source tree omitted fixes already present in the actual
production image, whose source label was
`152cdded8dd5ce2382976b6900f9adad844cf186`. A successful original r1 probe's
frozen response contains structured `cache_write_tokens_details`; the older
branch's all-values-must-be-integers check rejected that provider metadata.
The r2 connection probe failed with the same validation error, before the
compiler entered native execution. Its original upstream usage was not saved,
so the exact r2 field cannot be reconstructed. The worker and proxy were
actually stopped; original failed identities, response ledgers and stop
receipts remain intact.

The corrective source is root-repository commit
`4f1a8320ceba2e52e050495f46b30a5caeee73da`: all 35 production llm-service
files are retained, 30 as exact original Git blobs, with only the original
five-file output-budget patch applied to the remaining files. Native Pi
history, tool-result images, reasoning/phase history, route snapshots,
structured usage extensions, stream diagnostics and the original build
contract are retained. This restores deployed functionality; it is not a new
usage-policy relaxation. The full suite passes 159 tests and 450 subtests.

The corrected normal Git/Dockerfile build explicitly uses the original
production Python base digest. Image and running-Pod module hashes are checked
against the preserved source, alongside the original environment, ConfigMap,
Secret references, routes and defaults. Exact plan
`fee4ba14fc441f863c008aeeaf9fd89cc540e309eeb04535656b918f2fc3f629`
was applied to llm-service only. Its running image is
`arm-cluster-master:5000/llm-service:latest@sha256:db24099cf314cf842644a7d7c0eb0931f923d759e0f1cd42be9a67a75c312281`.
Health, eight actual runtime module hashes, the 32768/2048 bounds and unchanged
route/default hashes passed live checks.

## Current completion boundary

The corrected gateway is deployed from root source
`4f1a8320ceba2e52e050495f46b30a5caeee73da` at the `db24099c...` image digest
recorded above. Its live runtime checks confirm the explicit trusted 32,768
and guarded 2,048 limits, preserved production source and unchanged route
defaults. This restores the requested gateway boundary; it does not prove that
the provider produced a complete 32,768-token response. Old worker profiles
still request 8,192, and no omitted budget was changed.

The fresh `candidate-wkc-v2r3` completed successfully against this gateway,
using compiler source `52a2a0a1f99ebb6d79f0bfd8a5272d78cd232a23` and worker
image
`arm-cluster-master:5000/agent-service-pi:latest@sha256:b44de2f40d5060cdc57132dd9c87949b46f4b54984b3f81f773ef3678e33fdfc`.
Its 13 recorded model calls produced a valid output with two packages from
three frozen candidate records. Both the worker and proxy were actually
observed stopped with exit status 0. Independent review passed suite
`video.animation-wikiskill-compile-contract.v2`; its initial host admission was
explicitly disabled and performed no deployment. The accepted combination is
`1a27d9894219d5d35ed86947864c06d98e14c2cdd43d5b605fa850f1272a3ccf`.
The earlier r1/r2 failures and their different causes remain preserved.

A separate activation then updated the three Agent control roles before the
creation executor, all from compiler source `52a2a0a1...`, and verified all
four ready. Their actual images are:

- Control: `arm-cluster-master:5000/agent-service-control:latest@sha256:1b87c208a4e5c525e1aa844dbe4ff489bc37a0a727c5453336eba2a81f332f02`.
- Executor: `arm-cluster-master:5000/video-animation-creation:latest@sha256:94cfb734cf5f690323a75d5f78f89bed35857e0f021a6f228dbd8f0e9c868cdc`.

The existing caller's authenticated, read-only gateway preflight returned v2
`ready=true`, with the old v1 profile still ready. Existing callers, runtime
secrets, old bindings and the workbench/lab deployments were preserved. This
is technical compiler admission and runtime activation, not a business
WikiSkill release or formal-video adoption. It performed no new media or
subjective quality validation. The separate reader-call extraction contains
model tool-call proposals; it does not establish complete source reading or
model understanding.

The subsequent real whole-library operation
`3e05ffd5-5b7e-4757-916d-8aeef5e69bd8`, remote job
`70932374-26a0-4dfc-b27f-79b3ec7d7342`, uploaded all 503 input references
(502 frozen records plus their manifest). It used the admitted v2 combination
and input SHA-256
`60b986b0503e31671ba0664ee06e97518a39f18c64b689d7e4ff4573954ec7f2`.
The operation exhausted its 900-second deadline: business state `failed`,
error `animation_wikiskill_agent_timed_out`, remote state `timed_out` and
remote error `deadline_exceeded`. Its final receipt has
`remote_stop_proven=true`, `release_id=null` and no artifact manifest.
The final read-only observation records 58 completed HTTP-200 model requests
and one request in an admitted state; that observation made no model requests
or submissions itself. Successful transport and reader proposals do not prove
all 502 records were processed. The receipt establishes the timeout and real
stop; it does not isolate a specific underlying model or reader cause.

Thus the new reader, corrected gateway, stopped successful technical candidate,
independent admission and control/executor activation are verified. Complete
real-library compilation, new WikiSkill publication and formal-video adoption
remain unverified. No failed operation is relabelled successful, no automatic
retry or human acceptance is implied, and no OpenSpec task is checked off by this
document update.

The actual receipt directory is
`D:/github/armbianbegin/.research-temp/video-agent-development-20261112/effects-seven-optimize-20261116/`:

- Gateway: `llm-trusted-output-r2-apply-complete.json`.
- Candidate and independent review: `wikiskill-compiler-v2r3-candidate-summary.json`,
  `wikiskill-compiler-v2r3-independent-report.json` and
  `wikiskill-compiler-v2r3-reader-call-evidence.json`.
- Admission, activation and actual readiness:
  `wikiskill-compiler-v2r3-admission-summary.json`,
  `wikiskill-compiler-v2r3-apply-complete.json` and
  `wikiskill-compiler-v2r3-post-rollout-readiness.json`.
- Real-library outcome: `wikiskill-real-publication-v2-status.json` and
  `wikiskill-real-publication-v2-observation-20261116T074629Z-cd573f84.json`.
