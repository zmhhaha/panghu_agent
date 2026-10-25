# Scientific Pi API and research workbench

**2026-10-25 15:49:52 Asia/Shanghai deployed:** evidence-linked citations,
section indexing, private report versions and fixed revision-input previews are
online under OpenSpec change `add-scientific-evidence-linked-revisions`.
The server fetched fixed Git commits (application `e87eb0c`, release tooling
`26168fb`), built the API image and updated the paired API/UI source. Local
regressions passed 224/224, isolated ARM64 image checks 14/14, and production
operator API checks 13/13. Existing reports, worker and identity settings were
preserved. Real-user Casdoor browser acceptance remains pending. The
[implementation and release record](../../docs/scientific-research-pi/writing-workspace-20261025/README.md)
separates this deployed foundation from later Pi execution and adoption controls.

`GET /scientific-research/{task_id}/citations` requires the trusted authenticated
user, checks Pi ownership before reading worker records, and resolves published
report references against `papers`, `evidence`, `claims`, and `references`.
The response includes `schema_version=1`, `availability`, `reason`,
`report_sha256`, `citations`, and `warnings`. Each citation contains `n`,
`status`, `reason`, a public `paper`, `evidence`, and associated `claims`.
Claim assessments come from the research artifacts and do not constitute an
independent review. Internal full-text paths and abstract payloads are omitted.

The published report must match the manifest's report size/SHA256; the four
record files must pass their own size/SHA256 checks. Parsing is limited to
8 MiB of records, 2 MiB of report text, and 5,000 records per file. No record
downloads start if the manifest fails validation. Duplicate or invalid
identifiers, ambiguous JSON and broken relations cannot produce a trusted
trace. Only external HTTP(S) links without embedded credentials are exposed.

For an existing report, `availability=expired|legacy|unavailable` provides a
stable reason with no citation data; it never changes the published Markdown.
Missing identities return 401, unauthorized Pi reads return 404, a task without
a published report returns 409, and worker/storage failures remain retryable
503 responses. This endpoint creates no research, sends no email, and changes
no task or report. The UI requests it only when the user opens citation details.

## Stage 2 local interfaces: sections, versions and revision previews

All routes below require the trusted `X-Forwarded-User` supplied by the existing
authenticated internal proxy. Pi report ownership checks both report and task
IDs, including aliases. Private version, snapshot and preview lookups filter by
owner; another user's identifiers return 404. These routes are not a public
identity gateway, and no request-body owner field grants access.

| Route | Behavior |
| --- | --- |
| `GET /scientific-reports/{report_id}/sections` | Read the original report and index exact UTF-8 byte ranges; no version or snapshot is created |
| `GET /scientific-reports/{report_id}/versions?limit=50&offset=0` | List this user's existing private version metadata; an empty result does not import the original |
| `POST /scientific-reports/{report_id}/revision-previews` | Fix a private parent version, recorded-evidence snapshot and idempotent modification intent; starts no worker/model task |
| `GET /scientific-report-versions/{version_id}` | Return this user's private version metadata and exact content |
| `GET /scientific-report-versions/{version_id}/sections` | Index the selected private version without writing |
| `GET /scientific-report-versions/{version_id}/download` | Download that version's original UTF-8 Markdown bytes with `nosniff` |
| `GET /scientific-report-snapshots/{snapshot_id}` | Return snapshot metadata, integrity digest, expiry, pin state and material policy; omit stored report/record payloads |
| `GET /scientific-revision-previews/{intent_id}` | Restore an unexpired modification intent; still reports `dispatch_available=false` |

Section metadata includes `section_id`, `title`, `level`, `kind`,
`start_byte`, `end_byte`, range `sha256` and `editable`, alongside the full
`report_sha256`. IDs bind the complete report and exact range. Repeated titles
have different IDs; a parent includes its subsections and ends before the next
heading of the same or higher level. ATX/Setext headings, preambles and original
Unicode/CRLF bytes are supported; fenced and indented code do not create report
sections. Indexing is limited to 2 MiB and 2,000 entries. A stale report SHA or
foreign section is a 409, not an approximate heading match.

Preparing a preview requires `Idempotency-Key` (1..128 printable ASCII characters)
and a body containing `base_sha256`, `section_id`, `instruction` (1..2,000
characters after validation) and optional integer `timeout_seconds` (60..3,600,
default 1,800). Extra fields are rejected. The server resolves the parent and
section, verifies the report-bound evidence, and persists the intent only with
a consistent snapshot. Identical retries recover the same intent; a changed
fingerprint with the same key returns 409. Empty sections cannot be edited.
Shared legacy originals remain readable, but a report without Pi evidence
cannot prepare a revision.

The preview response contains the parent version, selected section, intent and
public snapshot metadata. Both preview creation and retrieval explicitly return
`dispatch_available=false`. This stage fixes inputs; it does not execute Pi,
send email, publish a candidate or change the user's adopted report. The original
report and existing Markdown download remain untouched. A new private base is
created only by the explicit preview operation, not by GET section/history
requests.

Snapshot `material_policy=recorded_evidence_only` means the fixed report plus
`papers`, `evidence`, `claims` and `references` records, checked against their
manifest. It does not copy original full-text files. A record may describe
findings extracted from full text, but an internal `full_text_path` is not a
public file or a promise that the source text is in the snapshot. Public snapshot
routes expose metadata only. Missing/expired source evidence blocks preparation
unless an existing valid snapshot can be reused.

Default `ReportVersionStore` limits are constructor settings, not new browser
controls or environment variables:

| Resource | Default limit |
| --- | --- |
| One report / serialized snapshot payload | 2 MiB / 16 MiB |
| Snapshot lifetime / preview-intent lifetime | 30 days / 1 day |
| Retained snapshot payloads per owner | 10, up to 64 MiB total |
| Retained snapshot payloads globally | 256 MiB total |
| Private versions per owner | 100, up to 32 MiB of original content |
| Private versions globally | 256 MiB of original content |
| Intent records per owner / unexpired active intents | 10,000 / 100 |

Quota checks run in the conditional INSERT statement. Snapshot payload byte
limits use serialized payload size; version quotas count original UTF-8 content.
Versions are append-only and have no automatic TTL deletion. Quota exhaustion
returns 429 rather than evicting report history.

The normal API reconciliation loop independently calls `cleanup_expired()`;
preview preparation also invokes cleanup. An expired snapshot is pinned while
an unexpired intent in `prepared`, `queued`, `running`, `validating` or
`publishing` references it. Cleanup atomically clears only expired, unpinned
payloads, releasing payload quota while keeping digest metadata and idempotency
tombstones. It does not delete versions, modify source reports or recycle keys.
Expired snapshot/intent access returns 410; reusing an expired idempotency key
does not silently create a fresh input. Integrity/conflict errors return 409,
and temporary writing-storage failures return a sanitized retryable 503.

`ReportVersionStore.append_candidate()` is an internal registration hook for a
future already-published revision report/task. It is not exposed as an HTTP
draft-upload endpoint. Pi execution, whole-report revision validation, version
comparison and explicit adoption remain separate implementation stages.

**2026-10-23 deployed at 21:01:45 Asia/Shanghai:** new research runs exclusively on Pi.
The API preserves read access to old CrewAI tasks and shared reports, while the
UI adds requirement clarification, plan confirmation, progress, cancellation,
and report/evidence downloads. API and UI were switched together; worker spec
was preserved. Real Pi completion/cancellation, all eight artifact hashes and
eight deployed Gradio HTTP checks passed. Browser clicks, automatic Timer and
real login-session acceptance remain outstanding because no browser was available.
The [workbench record](../../docs/scientific-research-pi/workbench-20261023/README.md)
tracks its deployment evidence and remaining browser acceptance.

The [2026-10-22 rollout](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md)
describes the earlier deployment with optional Pi and a CrewAI default. Its
deployment and test results are historical; use the workbench record for this release.
Component and fixture checks do not certify scientific accuracy or real-model
availability.

## Integration interfaces

- `app.api.scientific_agent.create_app(database=None, worker=None,
  projection=None, start_reconciler=True, versions=None)` returns the FastAPI application.
  `app` remains available for the existing ASGI entry point. Import and factory
  construction perform no network calls; the lifespan initializes storage and
  starts reconciliation. An injected projection supplies its database and worker
  when those arguments are omitted. `versions` optionally injects a
  `ReportVersionStore`; the default uses the same database. The lifespan
  initializes its additive version/snapshot/intent tables. Construction and
  imports still perform no network calls.
- `PiClient(base_url, token, transport=None, timeout=10)` uses exact `/v1` worker
  routes, including authenticated health. The injected transport receives
  `(method, url, headers, JSON_payload_or_None, timeout)` and returns
  `(status_code, headers_dict, body_bytes)`. It must not retry writes internally.
  `WorkerError` exposes `status`, `code`, and `retryable`. Upstream exception text,
  credentials, URLs, and arbitrary error messages are not forwarded to users.
- `PiProjection(database, worker, skill_revision, runtime_revision, notifier=None)`
  exposes `initialize`, `prepare`, `dispatch`, `get`, `publish`, `notify`, and
  `reconcile`. It exclusively uses `database._execute/_query` and fixed
  `scientific_pi_index`, `scientific_notifications`, and `scientific_reports`
  tables. It does not edit the shared SQLite helper or worker queue.
- `ReportVersionStore(database, ...)` stores private append-only report versions,
  bounded snapshots and fixed-input intents in `scientific_report_versions`,
  `scientific_report_snapshots` and `scientific_report_revision_intents`. Its
  public API routes perform owner filtering; the store never starts Pi or
  publishes/overwrites `scientific_reports`.
- Worker methods used are `put`, `get`, `cancel`, `pending(cursor=None)`, `result`,
  `ack`, `artifacts`, `artifact`, and `health`. Pending publication is
  `{items:[TaskSnapshot],next_cursor:null|string}`; artifact listing is
  `{items:[metadata]}`. Acknowledgement binds `attempt`, `result_digest`, and
  `report_id=task_id`.

Configuration for new Pi submissions:

| Variable | Meaning |
| --- | --- |
| `SCIENTIFIC_DEFAULT_ENGINE` | Image/manifests set `pi`; the API always submits Pi even if an old value remains |
| `SCIENTIFIC_PI_WORKER_URL` | Internal worker base URL without `/v1` |
| `SCIENTIFIC_PI_WORKER_TOKEN` | Dedicated controller Bearer token; never sent to browser |
| `SCIENTIFIC_PI_SKILL_BUNDLE_REVISION` | Required pinned skill revision |
| `SCIENTIFIC_PI_RUNTIME_REVISION` | Required pinned runtime revision |
| API model credentials | None required; model configuration and credentials belong to the worker |

API launch remains `uvicorn app.api.scientific_agent:app`. The worker is a
separate service launched by package B/E; the API does not start a Pi process.
Worker health uses actual `alive`, `queue_writable`, `runtime_ready`, and
`worker_ready` booleans. An environment variable alone does not imply readiness.
`GET /scientific-health` reports `backend=pi`, `llm_configured=null`,
`llm_configuration_source=worker`, and `model_probe_performed=false`. The API
does not inspect the worker's model secret or make a live model request.

The [dedicated API image](../Dockerfile.scientific-pi-api) uses pinned Python
dependencies from `scientific_agent/requirements-api.lock`, with no CrewAI,
model SDK, Node runtime, or academic tool packages. It copies the API, Pi
contracts/client/projection, citation/section/version modules, and standard-library
SQLite/mail HTTP clients.
Pi modules load directly from `/app/scientific_agent`; there is no
`/opt/scientific-pi-extension` fallback or package-path mutation.

The migration patch removes the old whole-package workflow mount, old API
entrypoint mount, RAG initializer, API LLM/RAG credentials and call labels.
Old ConfigMap objects remain available for rollback. Missing Pi modules fail
import instead of silently recovering through a retired overlay. See the
[deployment procedure](scientific-pi-deployment.md) for the API-only migration,
drain check, and complete Deployment rollback snapshot. API and UI must switch
together during a maintenance window: their identity contract changed.

## Public behavior and compatibility

Existing submit, task lookup, report search/detail, Markdown download, and health
paths remain. Submissions still return HTTP 200. History remains an array,
`keywords` remains a JSON string, and report IDs remain the task IDs. Request
fields accept optional `engine=pi`, `mode=brief|review`, and integer
`timeout_seconds=60..3600` (default 1800). Topic is trimmed before the 1..500 length
check; email/user ID retain their 200-character bounds. Omitting `engine` selects
Pi; explicit `crewai` returns 422 without creating a legacy task or thread.
Startup does not rewrite historical legacy task statuses, and Pi records never
enter legacy stale-task cleanup or lazy timeouts.

The API is an internal service behind the existing authenticated UI/proxy
boundary. UI callbacks read the proxy-injected `X-Forwarded-User` header without
depending on header capitalization and forward it on each internal API call.
The API does not validate a Casdoor JWT itself and must not be exposed as a
public gateway accepting arbitrary identity headers.

For a new submission, the header must be nonempty after trimming, at most 200
characters, and contain no control characters. Missing identity returns 401;
invalid identity or a nonempty body `user_id` differing from the header returns
422. The body field is optional and is never the authority for user identity.

Pi task lookup, cancellation, artifact listing/download, report detail, and
Markdown download require the owner identity. Cross-user access returns 404
before contacting the worker. Report ownership checks both report ID and task
ID, including independently named report aliases. History filters other users'
Pi records before applying `LIMIT/OFFSET`, with deterministic date/ID ordering.
Legacy tasks and reports retain their shared read semantics; an anonymous
internal history query returns only legacy shared reports.

Pi submissions support `Idempotency-Key` (1..128 printable ASCII characters).
A fixed namespace plus user/engine/key produces the stable task UUID. Input
hashing uses the shared contract. A replay returns the original job, including
its original timestamps and revision pins; changed payload or changed email
returns 409. Email is only notification configuration and is not sent to Pi.

Dispatch intent is stored and read back before worker contact. `prepared` and
`ambiguous` may be recovered with the same ID; `accepted` is queried;
`rejected` is never submitted again. Definitive GET or PUT 409/422/429 records a
rejection. An unknown transport outcome returns HTTP 503 with structured
`detail={code,message,task_id,retryable:true}` so the UI can retain a recoverable
ID. No Pi record enters legacy task cleanup or legacy lazy timeouts.

The worker must persist rejection tombstones for new IDs before returning a
definitive rejection. This closes the crash window where the API receives a
rejection but its index update fails: recovery GET returns the original
409/422/429, and the API records rejection without sending PUT. The same ID must
stay rejected even after the original user-busy condition clears. Accepted-ID
input conflicts must not replace an accepted worker record.

Public status is always mapped from detailed Pi state:

| Detailed state | Public status |
| --- | --- |
| queued | pending |
| running, cancel_requested, validating, publishing | running |
| succeeded | done |
| failed, cancelled, timed_out | failed |

Task responses retain old `id/topic/status/user_id/report/error` and add engine,
mode, state, stage, progress, attempt, revision, report ID/digests, notification
status, and timestamps. Unknown progress stays null. A successful query reads
the report back from shared storage; storage failure gives a retryable 503.

Cancellation is `POST /scientific-research/{id}/cancel`; worker state decides the
cancel/finish race. Publishing and terminal outcomes are not revoked.
Artifact routes proxy only UUID task IDs and opaque `[A-Za-z0-9_-]{1,80}` artifact
IDs; metadata is restricted to artifact_id/media_type/size/sha256. Paths and
control headers are never forwarded. Download responses force attachment and
`nosniff`; no browser-supplied filesystem path is used.

## Recoverable publication and notification

An immediate startup scan and a periodic 10-second daemon scan run independently
of browser polling. Each scan recovers unresolved dispatch and walks every page
of pending publications, starting fresh at the beginning on the next scan.

For a publication, the API verifies task/attempt identity and the exact UTF-8
report SHA-256 and shared CanonicalJSON result digest. It uses scientific-only
`INSERT ... ON CONFLICT(id) DO NOTHING`, reads back and verifies task ID and
report hash, then sends publication-ack. Report content is never normalized or
replaced. Missing readback is storage_unavailable; existing different content is
publication_conflict. Both leave the worker publishing and publicly running.
A restart after insert but before ack safely repeats readback and acknowledgement.
There is no claim of a transaction spanning the shared database and worker.

Notification is separate from report success. A conditional notification claim
prevents concurrent sends, and `sent`/`unknown` are persisted independently.
An exception, an explicit false result, or a crash during send can mean mail was
already delivered; this is surfaced as unknown and is not blindly retried.
Recovery can finish notifications after an acknowledgement succeeded but its
response was lost. Mail failure never turns a successful report into failed.
For fixtures/canaries omit email to avoid sending mail.

## Research workbench behavior

Users clarify the research question, years, audience/use, required coverage,
and exclusions, then preview and explicitly confirm the plan. The combined
research topic must fit 500 characters; overlong input is rejected without
truncation. Brief/review use Chinese labels. The UI offers 10/20/30/60-minute
execution limits, defaulting to 30 minutes, plus optional notification email.
There is no user-facing engine selector.

Preview does not create a research task. Any input change invalidates the
confirmation; submission also checks a fingerprint of all complete fields on
the server, so a missed or delayed change event cannot submit an older plan.
The confirmed plan retains one `Idempotency-Key` across ambiguous retries and
repeated previews. A successful repeat queries the known task. A definitive
rejection or terminal task requires a new explicit preview before another
attempt. Missing login identity prevents submission.

Submission, manual query, cancellation, and each five-second `gr.Timer` tick
are independent bounded requests. They do not occupy a long-running generator
or stop polling after a fixed 20-minute UI timeout. Actual states/stages are
translated for display; a null percentage remains unknown. Cancellation uses
an independent nonqueued event and distinguishes request received, stopped,
and publication already irreversible. Closing the page does not cancel the
background research; a retained task ID restores the view.

Each browser has its own deep-copyable Gradio State bound to the authenticated
identity and browser session. View revisions discard old in-flight timer
results after switching tasks. Editing the task ID pauses old tracking.
Attachment selection revisions similarly discard obsolete download responses.

Successful tasks offer exact UTF-8 Markdown and a whitelist of research
attachments: report, searches, papers, screening, evidence, claims, references,
and validation. Attachment downloads verify size and SHA-256 before exposing
a file. HTTP 410 explains that attachments expired while the published Markdown
can still be obtained from report history. Legacy report lookup/download and
the support-author tab remain available.

Download buttons receive existing temporary files, reused within the session
for identical task/content/suffix. An initialization callback registers Gradio's
state deletion callback without replacing the already usable initial State;
session cleanup deletes only its own generated source files. Gradio's copied
file cache is scanned hourly to remove files older than 24 hours. Download-file
creation failure does not remove an otherwise completed report from the view.
Keep the existing HTTPS `GRADIO_ROOT_PATH` deployment setting.

## Local verification

Run from the panghu_agent checkout, without installing pytest:

```sh
python -m unittest discover -s tests/scientific_pi/api -v
python -m compileall -q app/api/scientific_agent.py app/ui/scientific_agent.py scientific_agent/pi_client.py scientific_agent/pi_projection.py tests/scientific_pi/api
```

The current candidate has **36 passing UI behavior tests**, including stale
confirmation rejection, trusted identity forwarding, stable submission retries,
timer/download races, legacy downloads, file integrity, and session-local
cleanup. Compilation and targeted `git diff --check` passed. API coverage also
includes ownership and aliases, history filtering before pagination, CrewAI
submission rejection, import without CrewAI, durable dispatch, publication
recovery/readback, notification failure, and worker protocol compatibility.
The [candidate record](../../docs/scientific-research-pi/workbench-20261023/README.md)
and its Linux ARM64 receipt retain the broader test results. Historical test
totals from the optional-Pi implementation are not current candidate results.

**Real Gradio 6.19.0 HTTP acceptance passed 13 checks**, recorded in the
[HTTP receipt](../../docs/scientific-research-pi/workbench-20261023/evidence/gradio-http-receipt.json).
These use the actual Gradio components, state, callback serialization and file
routes with a loopback fixture and fixed test identity. They verify initialization
before load, integer dropdown values, changed-plan rejection, progress and
terminal timer callbacks, cancellation, recovery, independent sessions, history,
and exact report/attachment downloads (126 and 42 bytes). They make no model
requests, send no mail, and do not access production report storage.

**DOM rendering, actual clicks, frontend automatic timer scheduling, and narrow
viewport layout remain unverified.** The coordinator's browser tool failed to
start; a separate browser attempt started the tool but found no available
browser. The latter [blocker receipt](../../docs/scientific-research-pi/workbench-20261023/evidence/browser-tool-unavailable-pi-ui-review.json)
records `No browser is available` and an empty browser inventory. Successful
HTTP timer callbacks are not a browser rendering or automatic-tick pass.

UI unit tests use a small Gradio stub and do not establish rendering. Worker
HTTP fixtures inject a ready runtime and preconstructed results, so they do not
certify model execution or scientific output. Candidate release still requires
the remaining deployment/browser checks recorded above; the earlier production
rollout cannot substitute for those checks.
