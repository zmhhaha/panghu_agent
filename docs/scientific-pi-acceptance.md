# Scientific Pi integration acceptance

Current production rollout, final image/suite counts and real-source report
evidence are recorded in the [deployment record](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md).
The observations below retain their original test scope and are historical
unless a later dated section explicitly updates them.

Earlier status on 2026-10-22: **18 publication integration tests passed**, the new
actual-Pi controlled-research test **passed on ARM64**, and a **real-provider
controlled-research probe passed** with synthetic sources and an isolated report
store. These are distinct checks, not a single combined suite run. The original
local suite had also passed 19 tests, including its actual pinned Pi CLI smoke.
The latest 18-case rerun after the harness cleanup correction passed in **4.747s**.
Production report-store readiness, real-source research quality, and deployed
acceptance remain separate gates.

The implementation here comprises the [publication integration tests](../tests/scientific_pi/integration/test_end_to_end.py),
[local harness](../tests/scientific_pi/integration/harness.py),
[actual CLI smoke](../tests/scientific_pi/integration/test_real_pi_smoke.py),
[controlled-research test](../tests/scientific_pi/integration/test_real_pi_research.py),
and [opt-in live probe](../tests/scientific_pi/live/research_probe.py). Worker/API
implementation changes and production deployment are documented by their owners.

## Reproduction and observed result

Run from the panghu_agent checkout:

```bash
python -m unittest discover -s tests/scientific_pi/integration -v
```

Original local combined run, before adding the controlled-research case:

```text
Ran 19 tests in 8.645s
OK
```

Environment: Windows, Python 3.13.5, Node 24.21.0, reviewed Pi 0.73.1 and existing
installed FastAPI/Starlette TestClient and SQLite. The parent installed the pinned
npm tree beforehand; F did not install packages. No real model/source/service
credentials were needed. A pre-existing Starlette warning about its current httpx
TestClient compatibility was emitted; it did not fail the tests. The suite uses
unittest, not pytest. The 18 publication tests use ASGI without sockets; the actual
CLI smoke starts only a temporary IPv4 loopback model socket and real Pi/tool
subprocesses. It starts no production server.

## Actual pinned Pi CLI mechanism

[The actual CLI smoke](../tests/scientific_pi/integration/test_real_pi_smoke.py)
automatically skips if the parent-installed npm CLI or Node executable is absent;
a present but incompatible runtime fails the test. Reproduce it independently:

```bash
python -m unittest discover -s tests/scientific_pi/integration -p test_real_pi_smoke.py -v
```

The actual CLI-only run passed in 3.337s; the final combined result above includes
an additional assertion of actual skill discovery. The test uses the real
PiRuntime in fixture_mode, Node and the installed pinned CLI with a fresh temporary
HOME/cwd. It verifies the reviewed skill and bundle hashes before execution. A
loopback-only mock OpenAI chat-completions SSE server makes exactly two responses:
the first asks the actual Pi bash tool to execute `echo SCIENTIFIC_PI_LOCAL_TOOL_MARKER`;
the second model request must contain that actual tool result with the matching
call ID, after which the server supplies a final assistant message. The system
prompt must advertise the reviewed research-procedure skill and its exact path.
Tool start/end and assistant/agent completion events, actual Pi/Node/model/provider
provenance, dummy API-key environment reference, omission of the dummy key and tool
body from runtime events, and removal of the runtime cleanup journal are checked.
The wire deadline is 35s and the whole test is bounded below 45s. No report-output
validation or scientific finding is claimed for this smoke.

The first actual run caught a real adapter incompatibility: pinned Pi's
noninteractive version output was on stderr, while the version probe accepted
only stdout, so the test failed before any model requests. F relayed the concrete
source/runtime evidence to A through the parent; A added bounded exact version
handling. The smoke then passed without a test-side bypass. A's suspended Windows
process creation/Job Object assignment path was exercised successfully on normal
completion. This is not production Linux containment evidence.

## Controlled research through the actual CLI (new gate)

The [new integration case](../tests/scientific_pi/integration/test_real_pi_research.py)
connects the real Pi runtime to the existing full publication harness. A
loopback-only deterministic model requests three actual CLI turns: read the
pinned research-procedure skill, execute a bash/Python tool that writes the
explicitly synthetic research fixture and runs the real research `validate` CLI,
then finish. The model endpoint requires both tool results to come back before
it can complete. The scheduler independently validates and copies the resulting
files, and the API persists the report before acknowledgement. Assertions cover
paper/evidence/claim/reference links, exact CRLF/UTF-8 report download, each public
artifact digest, and no notification row or mail call.

This uses `fixture_mode=true` and a conspicuous zero image-digest protocol fixture;
it is neither production identity evidence nor a live-provider/scientific-quality
test. The supplied fixture report is not authored by a live model. The separate
live-provider controlled task below subsequently passed; a real-source canary
remains separate.

Run from the checkout or the test-only acceptance image:

```sh
python -B -m unittest discover -s tests/scientific_pi/integration -p test_real_pi_research.py -v
```

The first new-case run on Windows took 10.305 seconds and failed before any model
request with `503 runtime_not_ready`. This is consistent with the separately
recorded pinned CLI version-preflight timeout on this host. No timeout was
relaxed, passing result invented, or present-but-failing CLI skipped. Python AST
checks passed. The deployment coordinator then ran the new case against the
accepted ARM64 runtime: **1 test passed in 7.347s**. The changed harness and test
were mounted read-only over the test-only acceptance image, with loopback-only
networking and temporary storage. This confirms the test in that environment;
the Windows preflight failure remains recorded above.

## Live provider with a controlled source packet (opt-in)

[research_probe.py](../tests/scientific_pi/live/research_probe.py) extends the
existing fixed-destination model probe to a complete controlled research task.
The live model receives two explicitly fictional, contradictory abstracts and
the real schemas; it must read the installed skill, generate research artifacts,
and invoke the actual research validator. The ordinary runtime, scheduler,
controller validator, API projection and download then process those outputs.
The probe checks exact preservation of the source records, abstract quotations,
a conflicting claim referencing both evidence items, report limitations, all
artifact hashes, report publication/acknowledgement and no email. It uses a fresh
local SQLite facade, so success does not establish remote report-store readiness.

`--help` and `--self-test` passed on 2026-10-22 with **zero model/network calls**.
The self-test checks the source/evidence relationships, rejects an invented
finding, and verifies the bounded model-request limit. The actual provider run
subsequently passed on 2026-10-22; its measured results are recorded below.

The parent deployment coordinator can run it in a fresh isolated ARM64 Pod using
the test-only acceptance image. Mount current versions of
`integration/harness.py`, `live/model_probe.py` and `live/research_probe.py`
read-only at their existing `/app/tests/scientific_pi/` paths. This test requires
the acceptance image's HTTPX/API dependencies; the production worker image alone
does not contain them. Use the same five controller capabilities, read-only root,
scratch `/data` and `/tmp`, no service-account token, no production PVC, and only
DNS/model-gateway egress as the existing model probe. Supply the actual selected
image digest and a model credential by Secret reference; never place a credential
in command arguments or files. In this probe the real credential remains with
the root proxy; the task UID receives only a dummy loopback key. This proxy is
test-only: the production worker passes its dedicated model credential to the
task child and strips controller, database, Vault and mail credentials.

```sh
python -B /app/tests/scientific_pi/live/research_probe.py --report
```

Execution is limited to 12 model requests and 480 seconds; set Pod
`activeDeadlineSeconds` to 540. The output JSON records real Pi/Node versions,
model-observed metadata, chain/hash/cleanup results, elapsed time, child CPU and
Linux `RUSAGE_CHILDREN` memory high-water. Memory is not a container peak, and
unavailable token/cost data remain null. The saved
`/data/research-probe-result.json` contains sanitized evidence; successful
`/data/research-probe-report.md` contains the scanned synthetic report for human
review. Even a passing result has `production_acceptance=false` and
`scientific_accuracy_accepted=false`: it establishes live-model controlled-task
integration, with a separate real-source and production-store canary remaining.

### Observed controlled-provider result

The successful isolated Pod was `scientific-pi-research-probe-debug3-20261022`,
UID `a20edc8d-7a1d-44dc-a208-47a37b5808c8`, phase `Succeeded`. Its sanitized result
was saved to the outer repository at
`.research-temp/scientific-pi-research-20261022/result.json`.

| Observation | Result |
| --- | --- |
| Outcome | `valid=true`, `controlled_research_published` |
| Runtime | Pi `0.73.1`, Node `24.21.0`, Linux ARM64 |
| Test image digest | `sha256:944b3a3add1778b803fed88475d8e1ff171ef93873cd40c1859512e6dd27facf` |
| Provider/model | Configured alias `llm-trusted`; SSE metadata reported `gpt-6.1-sol` (provider self-report only) |
| Elapsed / requests | `143.213s` / 11 requests, within the 12-request bound |
| Actual tools / skill | `read`, `bash`, `write`; pinned skill read confirmed |
| Source/evidence/claims | 1 synthetic source, 2 papers, 2 exact abstract quotations, 2 claims; conflict linked to both evidence items verified |
| Publication | Real API projection into isolated in-memory SQLite facade, 1 ack; exact report/download and all artifact digests verified |
| Process/credentials | Task UID `200000` cleaned; real proxy key absent from task workspace and result |
| Email | None |
| Child CPU | User `7.83s`, system `1.053s` |
| Child memory | `181828 KiB` Linux `RUSAGE_CHILDREN` high-water, not container peak |
| Tokens / cost | Unknown (`null`) |
| Report SHA-256 | `bb2c667c9f3c975b1018aff8dea84a714b78bee392a5c5e1713a5f2c548b50d0` |
| Canonical result digest | `ed29e84187ea478451a7676b9ba35fb24d4ddbec56e2d5afd3b9f78a6c993317` |

The successful run had `fixture_mode=false` and used a real provider, while its
source packet and readiness declarations were explicitly synthetic. It retained
`production_acceptance=false` and `scientific_accuracy_accepted=false`.
The report bytes were checked during execution, but the memory-backed emptyDir
was reclaimed before the Markdown could be exported after exit. No later human
review of an exported report is claimed. Future runs needing a human-review copy
must collect that artifact while the Pod is still running.

Two probe/harness defects were found and corrected before this success:

- The harness unconditionally tried to chmod task-UID-owned files during cleanup.
  Linux's five-capability controller lacks `CAP_FOWNER`, causing `PermissionError`
  and masking the original result. The chmod loop now runs only on Windows;
  Linux cleanup uses the existing permissions. No capability was added.
- The probe imposed an 8 MiB total / 1 MiB line runtime-output bound below the
  production defaults. After a real skill read, its second diagnostic run failed
  with `research_runtime_output_limit`. Removing these probe-only overrides
  restored the existing 32 MiB total / 4 MiB line defaults; the next run passed.

The successful cumulative output size was not recorded, so the evidence only
establishes that this controlled task fit the 32 MiB bound. A later test-only
thread profile records the runtime's numeric byte counter without retaining
model/event content; it was not deployed in this successful run. No claim about
the capacity for a longer review follows from this result. All three diagnostic
Pods and their ConfigMaps/NetworkPolicies were deleted after collecting the
sanitized evidence (nine temporary resources total).

## Bounded Pi output transport correction — 2026-10-22

A later production canary failed with `runtime_output_limit`. Its adapter did
not retain numeric output accounting, so the exact proportion of source data,
tool output and repeated snapshots in that failed attempt cannot be recovered.
The previous controlled-provider success is not evidence that every real-source
task fits the original transport budget. The separate earlier `model_error`
canary also remains an unidentified upstream-stream failure; this output change
does not claim to diagnose or repair that provider failure.

Read-only inspection of the deployed Pi 0.73.1 `dist/modes/print-mode.js` confirmed
that its session subscription serializes every event with `JSON.stringify`.
For a `message_update`, the growing assistant appears in both `message` and
`assistantMessageEvent.partial`. Serializing the entire accumulated answer on
each token creates quadratic diagnostic output. The Python adapter previously
counted all those bytes against the same 32 MiB budget, despite retaining only
event metadata itself.

The image-owned [JSONL compactor](../pi_worker/runtime/jsonl_compactor.mjs) now runs
as a fixed explicit Node `--import` in the same isolated task child. The trusted
factory enables it; job input cannot select its path or change its limits.
The adapter verifies the script's pinned SHA-256 before running it, and Git
attributes preserve its LF bytes across checkouts. No installed `node_modules`
file is patched. The existing Pi/Node version preflight and task deadline remain
in effect.

The compactor preserves each assistant delta and complete terminal
`message_end`/`tool_execution_end` content. It omits the two repeated assistant
snapshots, intermediate cumulative tool results, and repeated history at
`turn_end`/`agent_end`. Assistant identity, stop reason and model error remain
available to the Python verifier. Malformed JSON, invalid UTF-8 byte fragments,
and unterminated lines fail closed; no malformed bytes are silently replaced.

The bounds apply independently:

| Bound | Limit |
| --- | --- |
| Raw pre-compaction JSON line | 4 MiB |
| Raw pre-compaction aggregate | 512 MiB |
| Raw event count | 100,000 |
| Compacted stream | 32 MiB |
| Physical stdout plus stderr consumed by Python | Existing 32 MiB aggregate / 4 MiB line |
| Execution duration | Existing task deadline and bounded process cleanup |

The larger pre-compaction budget is confined to repeated diagnostic snapshots,
and remains finite. For scale, an 8192-token generated segment of roughly 32 KiB
can produce about 128 MiB if one complete cumulative snapshot is serialized on
each token; Pi may include that snapshot twice. This is a sizing illustration,
not a measurement of the failed canary or a guarantee for a long review.
The next real canary must establish its actual consumption.

The compactor periodically emits only numeric accounting: raw bytes/lines,
largest raw line, compacted bytes/lines, largest compacted line, and counts of
message/tool updates. The controller separately counts all physical output bytes.
Success includes those values in provenance; failure logs the latest counts and
validated task UUID without prompt, tool payload, credential or provider error
text. A failure between accounting events may only have a partial raw count;
the physical-output counter is controller-owned.

The new [transport tests](../tests/scientific_pi/runtime/test_jsonl_compactor.py)
passed **6 tests in 1.005s** locally. A pinned event-shape fixture produces over
32 MiB of repeated snapshots but under 1 MiB after compaction, with every delta
and terminal message preserved. Other cases cover the actual Node preload and
physical pipe output; independent raw/line/event/output bounds; invalid JSON,
UTF-8 and incomplete lines; fixed script integrity; and terminal model errors or
wrong-model identity through the actual Python adapter. Sensitive fixture error
text appears in none of the exposed errors, event metadata or accounting logs.

The strict-gateway actual-Pi test now additionally requires nonzero real
`message_update` accounting and a reduction in output bytes. Its latest Windows
run still fails at the existing five-second version preflight before a model
call; no preflight bypass was added. The deployment coordinator runs this gate
on the ARM64 acceptance image before the next production canary. Unknown model
errors remain nonretryable; no broad retry of authentication or protocol errors
is introduced by this change.

## Actual local chain exercised

Both FastAPI applications run their real lifespans in separate TestClient
instances. The chain is:

```text
public HTTP submission
  -> PiProjection durable prepared intent in in-memory shared SQL facade
  -> actual PiClient serialization/authenticated transport
  -> real worker FastAPI route
  -> private QueueStore SQLite admission and claim
  -> actual Scheduler.run_claim with synthetic FixtureRuntime
  -> actual C research-output validator
  -> actual controller artifact copy, hashes, manifest and result digest
  -> publishing result via worker HTTP
  -> PiProjection report insert-if-absent and readback
  -> actual PiClient publication ack via worker HTTP
  -> succeeded/done, public report/history/download and artifact HTTP routes
```

The worker queue is a real temporary file-backed SQLite database. The shared SQL
facade implements the existing `_execute`, `_query`, initialization and report
helper API using actual in-memory SQLite statements; projection SQL, conflict
handling, readback and keyword JSON encoding are not mocked. Only the remote
shared SQLite HTTP service/transport is substituted. PiClient's injected transport
bridges actual worker ASGI responses and retains the real `/v1` protocol and
Bearer authentication. Transport failures are deliberately injected at documented
request/response boundaries.

The scheduler background polling loop and API periodic reconciler are disabled
for deterministic ordering. Tests explicitly claim and execute real scheduler
attempts and call real projection reconciliation. A running-cancellation test
uses bounded thread events to hold the fake runtime until the real scheduler
forwards its cancellation signal. This is signal/state testing; it is not OS
process-tree termination testing.

The fixture runtime loads C's existing
[synthetic research fixtures](../tests/scientific_pi/research/fixtures.py) by
explicit path, since the tests root is not a Python package. It writes the actual
required research artifacts and conspicuously synthetic runtime provenance.
The image digest consisting of zeros is a protocol fixture, not an available
image. Fixture model/Pi/Node labels explicitly state no real process or model call.
The report contains Chinese UTF-8 and explicit CRLF bytes to catch newline or
encoding changes through hashing, SQL persistence, API responses and downloads.

## Passing acceptance cases

| Test | Evidence established locally |
| --- | --- |
| `test_full_chain_validates_persists_acks_and_downloads_exact_bytes` | Public accepted submission is queued/pending, actual C validation produces publishing/running, report persistence precedes ack, then public state is succeeded/done with matching report ID. Search/details/download retain the report and JSON-string keywords. CRLF/UTF-8 download bytes and SHA-256 match the worker report; the canonical result digest recomputes; exactly one runtime execution and one ack occur. Pi never enters the legacy task table. |
| `test_report_store_outage_preserves_result_then_recovers_without_research_rerun` | Injected report INSERT failure keeps publishing/running and storage_unavailable, preserves immutable result digest and does not ack or expose a nonexistent report. New projection recovery publishes the same bytes with one report row and no research rerun. |
| `test_cancel_after_publishing_keeps_result_and_user_admission_until_ack` | Cancel after the publication boundary leaves publishing/result unchanged; same-user new task is rejected while publishing, succeeds after ack; cancel after success preserves success. |
| `test_lost_ack_before_commit_replays_existing_report_then_acknowledges` | A saved report with a lost ack request remains publishing. Cancellation cannot remove it. Recovery checks the existing report and acks without duplicate rows, REPLACE SQL, or runtime rerun. |
| `test_lost_ack_response_after_commit_preserves_success` | Worker ack commits before response loss. API recovery retains succeeded/done and exact downloadable report, without rerunning research or duplicating rows. No email is sent. |
| `test_accepted_submit_response_loss_recovers_original_task_without_second_put` | Worker accepts before PUT response loss; API returns a recoverable task ID and ambiguous intent. Reconciliation finds the same task without another PUT; replay and eventual publication retain that ID and one runtime execution. |
| `test_durable_rejection_prevents_execution_after_index_update_loss_and_capacity_recovers` | Worker durably rejects a busy-user ID; the API's rejected-index UPDATE then fails, returning 503 with prepared intent. After capacity frees, recovery sees the worker's rejection tombstone and marks the API intent rejected without another PUT or queued execution. Repeated GET/PUT of the rejected ID and public key replay retain 429; a fresh key may submit. |
| `test_existing_foreign_report_is_never_overwritten_or_acked` | A conflicting existing report remains unchanged, worker remains publishing, API records publication_conflict and sends no ack. |
| `test_invalid_citation_fails_actual_output_validation_and_cannot_publish` | An invented [999] passes through the real runtime output path into the real C validator, which fails the attempt. No report, ack or download is produced. |
| `test_manifest_foreign_path_is_rejected_before_publication` | A model manifest pointing toward the control database fails with invalid_artifact and produces no public report/artifact set. |
| `test_foreign_hardlinked_report_is_rejected_by_actual_controller` | A workspace report hard-linked to a foreign temporary file is rejected before publication; the foreign bytes are unchanged. |
| `test_artifact_proxy_whitelist_integrity_and_private_metadata` | Every registered public artifact downloads with matching byte size/SHA-256. Metadata omits paths/control token; responses have nosniff. Unknown control/session/environment/provenance IDs are unavailable, and invalid artifact-ID syntax is rejected. |
| `test_corrupt_controller_artifact_is_rejected_without_changing_published_report` | Deliberate post-publication artifact-file corruption causes artifact_corrupt/409 on artifact download. The separately persisted report download retains original bytes and digest. |
| `test_acquired_full_text_is_validated_and_hashed_but_not_publicly_downloadable` | An explicitly synthetic acquired text is safely copied into validator staging and retained privately. C full-text validation passes; controller provenance binds its path/size/hash and the result digest recomputes. The private text is not registered as a public artifact. |
| `test_publishing_is_not_timed_out_after_execution_deadline` | Advancing the queue's clock beyond execution deadline does not expire a publishing task; cancellation preserves publishing, and publication ack still succeeds without rerunning research. |
| `test_queued_cancel_never_runs_and_worker_unavailable_is_not_legacy_404` | Queued cancellation maps to cancelled/failed and prevents claim/runtime execution. Worker transport loss for an indexed Pi task returns retryable 503 rather than falling through to legacy 404. |
| `test_running_cancel_propagates_signal_and_no_result_is_published` | Public cancel becomes cancel_requested, actual scheduler forwards cancellation to the held fake runtime and terminates state as cancelled/failed; no report or publication ack appears. |
| `test_worker_authentication_and_stale_publication_ack_fail_closed` | Missing/wrong internal Bearer token yields 401. Ack for a different attempt yields 409 and cannot finalize publishing; valid projection publication subsequently succeeds. |
| `test_pinned_actual_cli_bash_tool_result_returns_to_loopback_model` | Actual Pi 0.73.1 and Node execute a harmless bash tool and return its result to a second loopback SSE model request. Reviewed skill discovery, actual runtime provenance, metadata-only events and normal subprocess cleanup pass; no real provider/scientific output is used. |
| `test_actual_pi_reads_skill_validates_fixture_and_publishes_exact_download` (ARM64) | Actual Pi reads the pinned skill and executes a deterministic model's tool instructions to write synthetic research artifacts and run the real validator. Controller copying, API projection, evidence links, report/download bytes, artifact hashes and no email pass. This case is separate from the historical 19-case run above. |

A review finding about missing private acquired-text files in controller staging
was relayed to B through the parent. B added safe private-input staging/hashing;
the acquired-full-text integration case above then passed. F made no fix outside
its assigned paths. The durable-rejection test also passed against B/D's new
rejection-tombstone implementation.

## What remains outside this evidence

The controlled-provider probe establishes the bounded real-model workflow
described above. The integration cases and that probe do not establish these
remaining gates:

- Scientific findings from real sources, production model-credential handling,
  OS cancellation of the actual Pi/tool process tree, or output-budget capacity
  beyond this short controlled task.
- Linux UID/environment isolation, cross-task/control-directory denial, orphan
  cleanup, operating-system process-tree cancellation, restart under kill/OOM,
  or actual periodic scheduling/reconciler timing.
- Deployed worker/API connectivity, real shared SQLite HTTP persistence/failover,
  Kubernetes probes, manifests, DNS/TLS/egress policy, PVC ownership/retention,
  production ARM64 rollout, deployed image digest or rollback execution. The
  specific test-only ARM64 image is identified in the successful probe above.
- Live public academic source availability, truthful full-text acquisition,
  source authenticity, identifier resolution, scientific correctness, review
  completeness or a defensible systematic-review process.
- Browser/UI behavior, legacy CrewAI execution, email delivery/idempotence,
  resource/cost/token comparisons, or end-to-end canary readiness.

The deployment/security, UI/legacy and live-source gates remain separately
documented. The local integration suite uses no real model credentials or
non-loopback model/source requests. The explicitly opt-in provider probe used
the authorized dedicated caller reference through its fixed-destination proxy
and isolated temporary Kubernetes resources. It sent no mail and changed no
production deployment, Secret, database or user data. Temporary test directories
and databases are removed by the harness; making read-only fixture files writable
is confined to Windows cleanup.
