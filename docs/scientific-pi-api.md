# Scientific Pi API/UI integration (package D)

Package D adds the optional Pi backend while keeping CrewAI as the default.
The dedicated API and UI were deployed on 2026-10-22. Current deployment and
acceptance results are in the [rollout record](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md).
The component tests below establish their stated contracts; they do not certify
scientific accuracy or replace real source and deployment acceptance.

## Integration interfaces

- `app.api.scientific_agent.create_app(database=None, worker=None,
  projection=None, start_reconciler=True)` returns the FastAPI application.
  `app` remains available for the existing ASGI entry point. Import and factory
  construction perform no network calls; the lifespan initializes storage and
  starts reconciliation. An injected projection supplies its database and worker
  when those arguments are omitted.
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
- Worker methods used are `put`, `get`, `cancel`, `pending(cursor=None)`, `result`,
  `ack`, `artifacts`, `artifact`, and `health`. Pending publication is
  `{items:[TaskSnapshot],next_cursor:null|string}`; artifact listing is
  `{items:[metadata]}`. Acknowledgement binds `attempt`, `result_digest`, and
  `report_id=task_id`.

Configuration for new Pi submissions:

| Variable | Meaning |
| --- | --- |
| `SCIENTIFIC_DEFAULT_ENGINE` | `crewai` by default; explicit request engine overrides it |
| `SCIENTIFIC_PI_WORKER_URL` | Internal worker base URL without `/v1` |
| `SCIENTIFIC_PI_WORKER_TOKEN` | Dedicated controller Bearer token; never sent to browser |
| `SCIENTIFIC_PI_SKILL_BUNDLE_REVISION` | Required pinned skill revision |
| `SCIENTIFIC_PI_RUNTIME_REVISION` | Required pinned runtime revision |
| `LLM_BASE_URL`, `LLM_SERVICE_TOKEN` | Existing CrewAI configuration |

API launch remains `uvicorn app.api.scientific_agent:app`. The worker is a
separate service launched by package B/E; the API does not start a Pi process.
Worker health uses actual `alive`, `queue_writable`, `runtime_ready`, and
`worker_ready` booleans. An environment variable alone does not imply readiness.

The existing scientific workflow ConfigMap mounts the whole
`/app/scientific_agent` directory. The dedicated API image therefore copies its
three new Pi modules into `/opt/scientific-pi-extension`. Before importing those
modules, the API appends that literal directory to `scientific_agent.__path__`
only when it exists. Mounted CrewAI code and skills retain first precedence;
Pi modules keep their canonical `scientific_agent.pi_*` names and shared class
identity. This assumes the extension directory remains image-owned, outside
ConfigMap or writable mounts. No environment-selected import path is accepted,
and missing dependencies still fail import. Local checkouts without the image
directory use their normal package imports.

Overlay regression verification: **33 API tests passed**, including three fresh
processes simulating the mounted package and mapping only the fixed image path
to temporary host files. They verify CrewAI precedence, actual Pi module origins,
canonical class identity, no network on import, and explicit failure for absent
or incomplete extensions. This portable simulation does not replace a real
Linux image test with the production ConfigMap mount.

## Public behavior and compatibility

Existing submit, task lookup, report search/detail, Markdown download, and health
paths remain. Submissions still return HTTP 200. History remains an array,
`keywords` remains a JSON string, and report IDs remain the task IDs. Request
fields add optional `engine`, `mode=brief|review`, and integer
`timeout_seconds=60..3600` (default 1800). Topic is trimmed before the 1..500 length
check; email/user ID retain their 200-character bounds. Pi requires nonempty
trusted `user_id`. There is no automatic fallback to CrewAI on Pi errors.

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

## UI behavior

The UI now wires engine/default selection and Pi brief/review mode into submit,
uses optional email, exposes the task ID, and shows actual state/stage and unknown
progress. Manual ID query and Pi cancel actions work after automatic polling
stops. A 20-minute polling limit leaves the task running. Structured ambiguous
errors preserve the recoverable ID. Both new-report and history downloads use
validated UUIDs and portable temporary Markdown files. Ordinary reviews are no
longer automatically described as completed systematic reviews.

## Local verification

Run from the panghu_agent checkout, without installing pytest:

```sh
python -m unittest discover -s tests/scientific_pi/api -v
python -m compileall -q app/api/scientific_agent.py app/ui/scientific_agent.py scientific_agent/pi_client.py scientific_agent/pi_projection.py tests/scientific_pi/api
```

Final local run: **30 tests passed in 0.719 seconds**; compileall and targeted
`git diff --check` also passed. Expected fault-injection cases log deferred
publication/mail errors while their assertions pass.

Environment used: Python 3.13.5, FastAPI 0.138.2, httpx 0.28.1. The installed
Starlette emits an httpx TestClient deprecation warning; the tests work with the
available versions and no dependency installation was performed.

Coverage includes definitive rejection/no replay, prepared and ambiguous intent
recovery, idempotency/email binding, report storage failure/readback/conflict,
restart before ack and lost ack response, notification failure, legacy fields,
cancelled status, exact report bytes/history/download compatibility, no network on
import, precise worker paths/authentication, filtered artifacts, startup
reconciliation without browser polling, and UI callback wiring/recovery.
The worker HTTP tests bridge the real local B ASGI routes and store with an
injected ready runtime and preconstructed result. They verify API/worker protocol
integration and deliberately bypass runtime execution and result validation;
package F owns full-chain integration acceptance.

Gradio is not installed in this local interpreter. UI tests use a small Gradio
stub to verify callback arity, payloads, and rendering decisions; compilation is
also checked. Actual Gradio rendering, browser interaction, deployed shared
SQLite behavior, real mail delivery, and Pi/model/runtime execution still need
integration/deployment validation. Package D does not deploy, commit, edit
runtime/worker/research tools, or change the shared contract/helper.
