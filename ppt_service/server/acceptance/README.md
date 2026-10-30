# P4 human review module

This module records per-version visual review and actual Microsoft PowerPoint
manual checks. The website mounts it with the trusted `ReviewContextResolver`;
the versioned schema-3 migration installs its tables. Importing it creates no
tables, and saving a human record never calls a model or publishes a version.

The separate SQLAlchemy metadata contains `ppt_acceptance_streams` and
`ppt_acceptance_entries`. Record payloads are append-only through this API;
corrections and revocation are new records. The stream revision supports compare
and swap, and an owner/version-scoped idempotency key supports retry after a lost
response. Database administration/backup policy must separately protect these
tables; this module is not cryptographic protection against a privileged DBA.

## HTTP contract

* `GET /api/v1/projects/{project_id}/versions/{version_id}/acceptance`
* `POST /api/v1/projects/{project_id}/versions/{version_id}/acceptance/records`

Both return the current full acceptance snapshot. POST requires an
`Idempotency-Key` of 1–128 letters, digits, `_`, `.`, `:`, or `-`.
Use the **same key and unchanged body** after a lost response. A replay returns
the current snapshot, which may now be stale or revoked; it never appends again.
Changing a body with an existing key is a 409. A stale acceptance revision, source
binding, raster hash, or page roster is also a 409. Fetch current state and make an
explicit decision before sending a fresh command with a new key. Retryable
database contention is a 503 `acceptance_busy`; retain the same key/body.

POST bodies are strict discriminated unions: `visual_review`,
`powerpoint_review`, and `revoke`. Every command echoes `expected_revision` and
the full `binding`. Only human observations are accepted. Owner identity,
timestamps, programmatic proof, and `accepted` flags cannot be supplied.

`contracts/*.schema.json` and `*.fixture.json` are generated from the Python
contracts. Fixture content is synthetic and is **not** acceptance evidence. To
regenerate, run with `panghu_agent` on `PYTHONPATH`:

```sh
python -m ppt_service.server.acceptance.generate_contract
```

Each visual submission contains the complete, ordered page roster. Pages may
remain `pending`; `pass` requires no unresolved issues and `needs_changes`
requires a described issue. A preview is identified by its SHA-256. Preview URLs
are generated only as the same-origin existing raster route
`/api/v1/versions/{version_id}/slides/{number}/preview`. They cannot be supplied
by the browser or resolver. The host must ensure that route serves the exact
independently regenerated, hash-bound PNG described by the context, never an
Actor-provided screenshot. This module does not serve files or arbitrary paths.

PowerPoint review records the **human-reported actual application version and
platform**, and explicit open, content-edit, native-chart-edit, save and reopen outcomes. It
does not automate or prove those human actions. The version/platform are required
when an action passes or fails. Subsequent successful actions require a successful
open; reopen additionally requires a successful save. Only an authoritative zero
native-chart count permits `not_applicable` for chart editing. Every deck still
requires an explicit content-edit check, including chartless decks. A missing action
stays pending. Opening a PNG or passing programmatic checks never fills this form.

Revocation retains the original record and reason. Revoking the latest review
does not revive an older passing review. An explicit new review is required.
Older records remain available with derived `stale`/`revoked` flags.

## Trusted integration boundary

The host must inject `AcceptanceService(sessionmaker, resolver)` into
`create_acceptance_router(service)` and mount that router deliberately. The
router uses the existing `server.auth.authenticate`, including signed RS256 JWT,
trusted proxy peer, issuer/audience/signed-email checks and mutation Origin/CSRF
checks. No development identity or new auth bypass is installed. A different
`issuer` or `sub`, project, or version is a 404 after authentication.

The resolver receives the SQLAlchemy session, authenticated `Identity`, requested
IDs, and `for_update`. It must:

1. Load the actual project/version owner and reject missing, deleted or foreign
   resources. Never reconstruct eligibility from a browser/Actor request.
2. For reads and writes, lock the authoritative project, version and relevant evidence rows
   in a consistent order **in that same transaction**. Hold all locks until the
   service transaction commits. Frozen artifacts must be immutable while locked;
   database locking alone does not stop filesystem changes. Both GET and POST
   request `for_update=True`; GET never creates or mutates records. This serializes
   context reads with source invalidation. The history is loaded in one SELECT and
   its final entry supplies the response revision, avoiding mixed revision/history
   snapshots under PostgreSQL READ COMMITTED. A future shared-lock optimization
   must retain this consistency and the same lock order.
3. Recompute/check the immutable manifest, input snapshot, PPTX and full source
   fingerprint. Load independent preview hashes, exact page order and trusted
   chart inventory. The full fingerprint must cover every source/resource file,
   including design/spec files, not just SVG/PPTX.
4. Load the immutable validation evidence ID and its complete hash from the
   controller-owned store. Evidence identity is a binding reference, **not** a
   declaration that programmatic checks passed. Include engine, runtime revision,
   profile and checkpoint format. Changing engines never reuses a review.
5. Supply `source_revision` as a **monotonically increasing authoritative source
   revision** and bind it into `SourceBinding`. Advance it for any source,
   current-input, evidence, runtime, or eligibility invalidation, including later
   restoration. Never reset it when hashes revert. This prevents a previously
   stale review from silently becoming current again. If the existing project
   revision cannot guarantee this, core integration must provide its own counter.
6. Supply authoritative current input/source hashes separately. A mismatch marks
   the version stale. `needs_input`, failed and planning sources are unreviewable;
   a historical Actor `accepted: true` cannot change that. A verified frozen
   candidate may retain human observations while full programmatic work is pending.

`ReviewContext` is a frozen typed host object. There is intentionally **no default
resolver**, no arbitrary context-from-JSON endpoint and no callable generic
programmatic-success flag. A resolver implemented from untrusted reports would
break the security boundary and must not be mounted.

Schema installation belongs to `server.migrate` revision 3, never API startup or
an operator calling `create_all` against the live service. It rejects conflicting
review tables found under an earlier schema revision. PostgreSQL protects entries
with an UPDATE/DELETE rejection trigger, and runtime grants separately deny
entry UPDATE/DELETE/TRUNCATE and stream DELETE/TRUNCATE. Stream UPDATE remains
available for optimistic revision CAS. Corrections and revocations append entries.
The current project deletion flow revokes access and marks stopped cleanup state;
it does not physically purge this history. Physical purge requires a separate
reviewed lifecycle operation. SQLAlchemy transactions, unique constraints, CAS
and row locks remain in use; real deployment and test evidence are recorded
separately from these implementation guarantees.

## Status and remaining work

Visual and PowerPoint checks derive separate `pending` / `passed` /
`needs_changes` states. `human_status=passed` requires both sets to pass for the
same current bound source. Stale/revoked records cannot contribute.

Because P3's full exporter verification and trusted certificate/core persistence
are still pending, **`programmatic_status` is always `pending`,
`publication_authorized` is always `false`**, and `acceptance_status` is only
`pending`, `stale`, or `unreviewable`. The response explicitly retains
`independent_postflight_integration_pending`. A human pass does not complete P3,
accept a candidate, grant publication authority, or modify legacy results.

The mounted resolver checks owner-locked source rows and immutable controller
evidence rather than accepting browser/Worker declarations. Human observations
remain distinct from a separately requested review-only draft revision and cannot
authorize publication. Full visual and PowerPoint manual acceptance remains a
human task; installed code and synthetic regression results are not such evidence.

Isolated tests, from the repository root:

```powershell
$env:PYTHONPATH='D:\github\armbianbegin\panghu_agent'
python -m unittest discover -s panghu_agent/ppt_service/tests/server -p test_acceptance.py -v
```
