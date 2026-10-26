# Task-bound Pi model authority

Updated 2026-10-23. These are internal controller components, not a public Worker control API. Production PptProfile and website Pi dispatch remain disabled.

## Identity and authority

`worker/model_grant.py` defines a frozen `ModelIdentity(project_id,job_id,attempt,token,phase)`: canonical UUIDs, strict positive int attempt/token (no bool), and the existing PPT phase enum. A ModelGrant holds an immutable identity, distinct bearer credential, finite monotonic TTL in(0,600], finite request budget and concurrency limit. Renewal only accepts that exact identity before expiry; it cannot revive expiry/revocation or refund request budget. Permit release is idempotent and does not refund attempted upstream requests. No identity is taken from Worker headers or request JSON.

A 20ms monitor closes registered downstream/upstream raw sockets on expiry. It calls shutdown(SHUT_RDWR) before close outside the grant lock; late attachments are immediately closed. Snapshot and controller receipt contain identity/counts/state, not credentials. The credential authenticates the fixed internal proxy; the upstream provider credential remains in the trusted proxy process.

## Proxy behavior

`ModelProxyServer(..., model_grant=grant)` validates the exact grant type, matching bearer configuration, and matching operator ledger job/strict-int attempt/phase before binding. Grant mode does not fall back to legacy authentication. Default model_grant=None preserves existing Hermes proxy behavior, signatures, fake-connection contract and600second timeout.

Grant mode bounds accepted connections to8 before thread spawn and applies a1second header/body idle timeout (plus the shorter remaining TTL). This is an internal local-task proxy tradeoff, not a general external upload server. Shutdown closes accepted sockets including partial request bodies and model streams before sealing usage.

Before sending model HTTP, the grant path explicitly connects, attaches the live upstream socket, and checks the permit again. It keeps a captured socket across getresponse(), because Connection:close may detach connection.sock while HTTPResponse owns the stream. Each read/write checks the grant. A deferred-connect regression proves that a connection completing after revocation is closed without submitting HTTP.

DNS resolution may remain blocked until the OS returns or the process is stopped; this code does not claim to cancel libc DNS. The downstream is revoked promptly, late connection is prevented from sending model HTTP, and container/process supervision remains required. Revocation also cannot undo a provider request already submitted or guarantee that supplier billing stops immediately.

Grant interruption uses existing proxy_shutdown usage outcome and unknown usage semantics, preserving the ledger schema and exactly-once finalization. Known token counts are provider observations; the offline protocol values below are synthetic, not a supplier bill. Exhausted admission returns429 without connecting upstream or creating a usage request record.

## Controller lifecycle

`control/pi_model.py` supplies TaskModelProxy:

- Real-provider construction retains RuntimeConfig.require_paid_admission. The explicit for_offline_test constructor uses a fixed synthetic provider key and a trusted test connector.
- Exact host layout and attempt identity must match. Every authority parent is an ordinary root-owned directory without non-root write permissions (root-owned sticky system tmp is an explicit test exception). Authority directory is exclusive root0700; receipt0600 is fsynced/atomically replaced. Existing intent forbids silently recreating the same attempt and replenishing its budget.
- A fresh random per-attempt credential is the only proxy secret exported into RuntimeSettings child env. Provider, fixed model, Responses endpoint and text/image support are trusted settings; capability/model/isolation readiness flags are preserved unchanged, never enabled by this component.
- Usage is stored in private controller space and attributed to the fixed job/attempt/phase. Receipts store only a credential SHA256, immutable full identity and grant snapshot.
- heartbeat_and_renew first commits RepositoryQueue's fenced DB heartbeat. Cancellation, failed/stale heartbeat, rollback or receipt failure revokes authority. A stale caller with a different identity cannot revoke the actual owner's grant. Close revokes before closing proxy/server resources.

This seam is implemented and independently tested; it is not yet installed into live PiDispatch/DockerBackend provisioning, heartbeat and stop paths. The host lifecycle still must carry the same authority into the actual task container and revoke it before worker shutdown across recovery paths. Existing Hermes backend is untouched.

## Actual verification

Independent internal-network PostgreSQL16 test container, private tmpfs data, no host ports, isolated from the development site's DB:71/71pass,0skip,14.267seconds. Suites: grant11, existing proxy/usage41, task-model lifecycle9, queue10. Includes real silent SSE revoke/expiry, late connect with no HTTP,8slow-body limit and teardown, genuine PostgreSQL fencing/commit/cancel behavior, private credentials and no same-attempt recreation. Existing local actual-Pi test could not finish its CLI version probe and made zero proxy requests; it is not counted as passing. Actual CLI verification used the pinned Linux image below.

Final constructor hardening removed the ordinary constructor's private test-connector argument entirely: normal construction always checks paid admission. Only the explicit offline constructor accepts a callable, truthy test connector and a fixed synthetic provider key. Final targeted PostgreSQL lifecycle recheck:10/10pass,0skip,3.944seconds, including false/None connector fallback rejection. Its separate source archive is782f3291dfe4d98c08207e820be9c70404f4601ccb3b3852f67bce5f51bc9b21 (890880bytes), retained under `/root/ppt-pi-model-grant-20261023-a2/`. The prior71-test run and actual-Pi report retain their original matching source archive below; they were not overwritten.

Real Node24.21.0/Pi0.73.1 ran in the previously reviewed offline image2a09398d26a3, with actual nonfixture UID isolation and the existing readonly domain mount self-test. The proxy now used ModelIdentity(PROJECT,JOB,1,1,plan_initial),30second grant,2request budget and1concurrency. Two SSE requests completed with matching call IDs; a third valid authenticated request returned429 before backend contact. Usage carried job_id/attempt/phase. Grant closed with inflight0/requests_used2, the runtime UID had no remaining processes, and the container exited0/was removed.

Source archive SHA256 `f5669e3176c954940b9244bf655a3c7974fa71a1c6940584453ed6d89737419b` (880640bytes); report SHA256 `87290e6e7c9ab4401f4e92258ab9da95dbe7f58e50ed1b955288d04a6c5db8a5`. Retained server evidence root `/root/ppt-pi-model-grant-20261023-a1/`, actual report under its attempt audit. Model and usage responses were synthetic loopback in a network-none container. No external provider call, PPT generation, visual acceptance, live-site engine switch or publication occurred. Test readiness flags are still explicitly synthetic; PptProfile.production_ready=False.
