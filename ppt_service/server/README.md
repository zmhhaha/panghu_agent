# PPT Service API

This package owns persistence and the public API. It never invokes Hermes, starts a subprocess, parses Office/PDF, renders previews, or trusts a worker's success claim. The normal deployment uses `server.site:create_site`; `/healthz` reports application liveness. Model jobs require the configured execution pipeline and explicit external-model consent. Generated presentations retain their independent quality and PowerPoint acceptance requirements.

## Review records and draft revisions

The mounted `GET /api/v1/projects/{pid}/versions/{vid}/acceptance` and
`POST .../acceptance/records` routes persist source-bound human observations.
The resolver verifies owner, terminal job, immutable checkpoint and complete
retained evidence before exposing a context. These records do not publish a
version or assert that a human actually performed the reported PowerPoint steps.

`POST /api/v1/projects/{pid}/review-revisions` is a separate draft branch. It
requires `expected_revision`, an explicit `base_version_id`, `feedback`, optional
`pages`, the exact latest `review_binding`/`review_revision`, and a fresh
`external_model_ack: true`. Normal authentication, CSRF, idempotency, quota and
single-project admission apply. The original material/settings snapshot must
still match, and the real confirmed stage-two plan must remain bound. Only the
supported Hermes/PPT Master native checkpoint contract is restored; there is no
cross-engine fallback. Controller revalidation and full checkpoint copying occur
before launching a worker.

Its immutable job authority restricts output to another review draft. Original
versions/checkpoints retain `eligible_as_revision_base: false` and are never
rewritten. The existing `/revisions` route still requires a published baseline;
the repository also rejects published output from every review-only job. New
drafts bind their parent version and undergo the same stop proof and independent
programmatic verification. Visual review, PowerPoint checks and formal publication
remain separate pending gates.

From the `panghu_agent` directory, install the pinned `ppt_service/requirements-api.in` into an isolated virtual environment. Python 3.12/3.13 are targeted. Run `python -m ppt_service.server.migrate`, then `uvicorn ppt_service.server.app:create_app --factory --no-proxy-headers`. Migration is a separate explicit step; startup does not modify the database. Required environment: `DATABASE_URL` (PostgreSQL psycopg URL), `DATA_ROOT`. Authentication additionally requires `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` (HTTPS), comma-separated `TRUSTED_PROXY_CIDRS`, and `ALLOWED_ORIGINS`. Any account successfully authenticated by the configured Casdoor application may use PPT; there is no PPT invitation, email-domain or per-address access list.

The separate oauth2-proxy must forward the verified issuer's access/ID JWT in `Authorization: Bearer …`. Merely forwarding `X-Auth-Request-*` is insufficient. API network access must be restricted to proxy peers and the configured peer networks must be narrow. The application verifies issuer, audience, stable subject, expiry, issued-at and RS256 signature. Casdoor enforces email verification through its registration and login policies; the API does not additionally require or evaluate the `email_verified` claim. On every authenticated request the API requires a valid email in the signed JWT. Mutation requests additionally require an allowed exact Origin and `X-CSRF-Token` matching the `ppt_csrf` Secure SameSite cookie issued by GET `/api/v1/me`. There is no environment-switch auth bypass. Tests can explicitly override the `authenticate` dependency in a separate harness.

The site and oauth2-proxy no longer depend on a `ppt-owner` ConfigMap or `/owner/emails` mount. `INVITATION_EMAILS_FILE` is obsolete and ignored. Resource ownership continues to use verified issuer and subject; authenticated users cannot read or modify another user's projects or files.

## API contract

All owner checks bind both verified issuer and subject. Foreign resources return 404. Project input IDs, storage paths, worker/session identifiers, owner and jobs are never caller-selected.

- Create project: `{title,purpose,audience,page_count:8,language:'zh',style,requirements,external_model_ack}`. Page count 3–20. PATCH accepts the same complete document plus `expected_revision`.
- GET project includes `revision`, `input_snapshot_hash` (current), `assets`, historical `plans`, `stage`, `active_job_id`, `confirmed_plan_revision`, `latest_version_id`.
- POST asset accepts a multipart `file`; 25 MiB/file, 10 current assets, 100 MiB/project retained uploads. PNG/JPEG, UTF-8 Markdown, DOCX and PDF have bounded signature checks. Parsing remains `queued` until a separate sandbox validates the whole file, compression ratios, encryption, page count, and parse deadline. No claim of parsing success occurs on upload. External-model acknowledgement is required before upload or task admission.
- POST plans: `{expected_revision,feedback:'',phase:'plan_initial'|'plan_options'}`. Stage2 requires a confirmed Stage1 matching the current inputs. Planning feedback creates a new immutable job and ultimately a new plan revision.
- Confirm: `{expected_revision,input_snapshot_hash,selection}`. Initial selection is `{design_mode:'free_design',template_id:null}`. Managed templates are unavailable until an accepted catalog exists. Options selection is `{direction_id}` matching an actual planner-returned direction ID in `design.directions` or `design.options`. Optional `page_plan` and `production_parameters` must equal that selected immutable option; edits return `replanning_required`, requiring new planning. Confirmation preserves the selection and subsequent job snapshots bind it.
- POST generations: `{expected_revision,plan_revision}`. Only the currently confirmed Stage2 can queue `prepare`; the trusted controller chains a validated prepared checkpoint to `generate` without another website gate.
- POST revisions: `{expected_revision,base_version_id,feedback,pages:[]}`. Base version is mandatory and must belong to the same owner/project. Previous versions remain immutable and downloadable.
- Plan/confirm/generate/revise/cancel require `Idempotency-Key` (max128). Same issuer+sub+route+key and same canonical request return the original result. Changed request returns409. Task creation returns202 and `{id,job_id,project_id,kind,status,stage,event_cursor,attempt,error_code,base_version_id}`. There is no invented percentage or rate.
- Events: durable SSE, integer `id`, allowlisted event names, `{type,seq,payload}` data. `Last-Event-ID` or `after` replays newer events. A retained-floor violation gives410 `event_cursor_expired` with `action:'refresh_snapshot'`; future/invalid cursors reject explicitly.
- Versions list includes sanitized `{id,project_id,parent_version_id,status,created_at,slides:[{number,title,summary,preview_url}],download_url,preview_notice}`. Preview is only PNG. Downloads use no-follow reads and an attachment PPTX header. SVG is never served.
- Queued cancellation stops immediately. Running cancellation becomes `cancelling` and cannot become cancelled/release the execution slot until the controller proves all writers stopped.
- DELETE project returns202 `deleting`; it revokes publication immediately, requests cancellation, and only marks `cleanup_ready` after stop proof. It performs no destructive filesystem cleanup.

Uploading or editing settings increments the project revision, invalidates plan confirmation and sets `draft`. Historical versions remain accessible. Removing an asset retains its row and original blob for snapshot references and storage accounting; it excludes that asset from future inputs.

## Trusted controller integration

Use `Repository(session)` within `with session.begin():`. Public HTTP routes do not expose these methods. `migrate()` seeds the sole execution slot. The PostgreSQL admission advisory lock serializes admission/idempotency/quota operations; row locks and the partial unique active-job index defend project admission. The sole execution-slot row is locked when claiming; queued selection uses `FOR UPDATE SKIP LOCKED`. Slot expiry never implies old writers stopped.

1. `claim_next(lease_seconds=60)` atomically reserves slot and increments a monotonically increasing fencing token. Returns Job or None. Persist returned job ID, attempt and token in the controller's fixed task envelope. `input_snapshot` is immutable JSON with source asset IDs/storage keys/digests/settings/hash and confirmed selection; physical staging/copying is the controller's responsibility.
2. `attach_execution(jid,attempt,token,job_uid=...,session_id=...,run_id=...)` binds server-selected execution identifiers. A different already-bound Job UID is rejected. `heartbeat(...)` extends the lease but never clears cancellation.
3. `event(job,type,payload)` persists only explicitly allowed business fields; unknown event names/stages/error codes reject. Do not accept arbitrary worker event strings as trusted stage evidence.
4. `prove_stopped(...,job_uid=...,all_writers_stopped=True)` is called ONLY by the trusted controller after observing the entire matching Job/Pod UID set stopped, including descendants. HTTP stop success or agent completion is insufficient. A cancelling/deleting job becomes cancelled and releases its slot. Otherwise the task moves to validating while retaining its slot.
5. On an independently validated immutable private snapshot, `accept_plan(...,outline,design,checkpoint_key,checkpoint_manifest,independently_validated=True)` creates an immutable checkpoint and a new plan revision, succeeds the planning job and enters awaiting_confirmation. Stage2 planner output must contain its real choices; the API never fabricates directions/spec locks.
6. `accept_prepared(...,checkpoint_key,checkpoint_manifest,independently_validated=True)` succeeds `prepare` and creates a new immutable queued `generate` job. It reuses the existing quota reservation. The next claim gets a new global fencing token.
7. `publish_version(...,manifest,artifact_root,checkpoint_key,checkpoint_manifest,independently_validated=True)` is restricted to stopped validating `generate/revise` tasks. The trusted publisher must have independently checked quality, OOXML, editable native charts, source consistency, page count and all PNG previews on its private snapshot. Manifest: `{pptx_key:'projects/…/versions/…/published/deck.pptx',slides:[{preview_key:'…/1.png',title:'',summary:''}]}`. Checkpoint key must be under `projects/{id}/checkpoints/`; artifact root under `projects/{id}/versions/`. Both version and checkpoint become immutable DB baselines. The boolean is a trusted in-process assertion, never a worker callback claim.
8. `finish_failed(...,error_code=...)` requires a stop proof and never discards the last successful version. Execution-result codes: parse_failed, model_unavailable, execution_timeout, quality_gate_failed, render_failed, export_failed, execution_interrupted, storage_full. Before an Agent outbox or execution exists, request construction may instead fail with execution_task_too_large or execution_request_invalid; this releases the slot without inventing stop evidence and retains the confirmed plans/checkpoints.
9. `record_usage(jid,attempt,phase,outcome,...)` remains callable after cancellation/failure to preserve charged work. A known provider request ID deduplicates within job/attempt/provider. Missing price persists as `reported_cost:null,cost_status:'unknown'`; it is never converted to0. Supplier billing reconciliation remains separate.

Storage layout: `DATA_ROOT/projects/{project_uuid}/assets/{asset_uuid}/original`. Names are display metadata only. Controller-managed attempts, snapshots, versions and checkpoints also remain below server-generated project UUIDs. API source blobs must be mounted read-only into workers; workers must never write project parents/assets/published/checkpoints. Physical snapshotting, full blob parsing, trusted validation, stop evidence, storage accounting for controller-created outputs/checkpoints, immutable filesystem ownership, and asynchronous cleanup are controller responsibilities, not public API operations.

Project source/versions/checkpoints are retained until explicit deletion. Settings expose event/log/staging retention windows; no automatic project purge exists. Quotas are disabled until a trusted operator configures a period and limits; counters reserve once per new generate/revise submission, including eventual failure/cancellation. Period rollover/configuration belongs to the trusted quota operator, not an anonymous/user endpoint. No cost hard limit is claimed.

## Verification

`python -m pytest ppt_service/tests/server -q` exercises HTTP ACL, both gates, stale uploads, idempotency, cancellation/late publication, event cursors, safe downloads, unknown costs and auth/release rejection with an explicit dependency override. SQLite checks do not establish PostgreSQL concurrency. Set `TEST_DATABASE_URL` to a disposable dedicated PostgreSQL database to also run duplicate-admission/global-claim/immutable-checkpoint integration tests. Tests never call a model and do not start a server.
