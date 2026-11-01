# Final PPTX acceptance — 2026-11-01

## Scope and current evidence

This change adds independent checks for the exact delivered PPTX and connects
version-bound programmatic evidence, human observations and an explicit owner
publication decision. Passing automated checks does not establish desktop
PowerPoint compatibility or authorize publication.

The implementation at application commit
`3cee2b483112ed795de8689f72a0600bb0c4d86b` passed the server regression suite
described below. The isolated preflight against the existing delivered Version
also passed all seven programmatic gates. After runtime rollout, the actual
installed acceptance bundle passed the normal trusted evidence reader and
read-only acceptance projection at `2026-11-01T17:05:32Z`. All seven
programmatic gates are now passed in that deployed projection; desktop
PowerPoint acceptance and an explicit owner publication decision remain pending.

The earlier incident, successful normal website generation and owner-supplied
review remain documented in `quality-diagnostics-20261101.md`. In particular,
Version `b6b02806-506a-414e-a314-8d3ec230f9f2` has an owner-reported ten-page
source-SVG visual review. The reported PowerPoint COM activation failure
`0x80070520` produced no desktop open/edit/save/reopen evidence; it identifies
an environment blocker, not an established defect in the delivered deck.
Neither observation is a result from the new final-PPTX preflight.

## Distinct acceptance evidence

| Evidence | What it establishes | Remaining limit |
| --- | --- | --- |
| Independent source-SVG raster regeneration, delivery audit and native OOXML/chart inspection | Bound source previews, delivery checks and structural facts | Does not show how the delivered PPTX renders or behaves in desktop PowerPoint |
| Exact final-PPTX rendering | The bound delivered PPTX renders through LibreOffice Impress, PDF and PNG conversion, with measured tools and fonts | LibreOffice output does not establish Microsoft PowerPoint rendering or editing |
| Source postflight and export-build equivalence | Independent source/quality checks and semantic comparison with a fresh exporter build | Rebuilt output is comparison evidence; it never replaces the delivered PPTX |
| Per-page human visual observations | The owner's recorded observations for the current Version | Opening a preview does not submit a passing observation |
| Desktop PowerPoint observations | Actual opening, editing, saving and reopening in the named desktop environment | Requires a human record; structural checks cannot supply it |
| Explicit publication confirmation | The owner authorizes the currently bound, complete evidence set | No renderer, validator or generic completed flag grants publication |

The UI labels source-SVG previews and final-PPTX rendered previews separately.
Native shape, text, picture and semantic-connector counts remain structural
facts. Editable arrow shapes alone do not prove that connectors follow moved
modules. Native-chart editing may be explicitly marked not applicable only
when the trusted native chart count is zero.

## Independent final-PPTX validation

The validator renders the exact delivered file through:

`PPTX → LibreOffice Impress PDF → Poppler PNG`

It records the delivered PPTX hash, renderer version, immutable execution
image, font inventory and the configured, resolved and hashed tool paths.
The image owns `/usr/bin/libreoffice`, `/usr/bin/pdfinfo`,
`/usr/bin/pdftoppm`, `/usr/bin/fc-list` and
`/usr/lib/libreoffice/program/soffice.bin`. The check requires a complete,
ordered page roster, expected dimensions and nonempty PNG files. A successful
source-SVG render cannot substitute for this gate.

The acceptance request uses schema `ppt-service.acceptance-request.v1` and
the resulting receipt uses `ppt-service.acceptance-evidence.v1`. A fresh nonce
and evidence identity bind the typed source identity, job, attempt, fence,
immutable validator image and canonical original-request hash. The validator
rechecks the frozen source/checkpoint, complete manifest, approved
specifications, delivered PPTX hash and source fingerprint before checking
the candidate. It rechecks source preservation after producing the reports.

The required additional gates are:

- `independent_export_build_contract`.
- `independent_postflight_and_delivery`.
- `independent_final_pptx_rendering`.

They supplement the independent raster, delivery and native OOXML/chart
checks. Source/quality postflight and export-build equivalence remain separate
checks. A fresh scratch build is compared semantically with the original
delivered deck. Partial equivalence mismatches remain `needs_changes`;
warnings and delivery advisories remain pending. No mismatching result is
silently converted to a pass.

Programmatic receipts report `passed`, `pending` or `needs_changes` and retain
`publication_authorized=false`, `human_visual_acceptance=false`,
`powerpoint_acceptance="pending"` and
`full_exporter_postflight_verified=false`. Targeted gate completion must not be
described as a full exporter or desktop acceptance claim. Invalid runs expose
fixed error codes through `acceptance-error.json`; raw exceptions and source
paths are not public diagnostic messages.

## Source preservation and execution prerequisites

The original source, approved specifications, manifest, delivered PPTX, failed
job records, earlier validator receipts and human observations remain intact.
New reports and rebuilt comparison files are written to separate output.
Validation does not regenerate business content or retry interrupted
generation.

The coordinator is disabled unless explicitly configured with a compatible
Agent Service backend, immutable validator image and trusted execution oracle.
`PPT_ACCEPTANCE_ENABLED` is a controlled runtime prerequisite, activated only
after the runtime/source checks described below. The coordinator considers only
existing immutable review Versions with `ppt-agent-artifacts-v1` delivery.
It checks active jobs, the global execution slot and material processing,
releases source locks before execution, and rechecks idle priority before
launching at most one bounded independent validation per tick.

The validator has no model credentials, model request, business database
capability or publication capability. Its Pod denies network traffic in both
directions, mounts no service-account token, runs nonroot with capabilities
dropped and a read-only root filesystem, and uses ephemeral temporary/home
directories. Candidate and request mounts are read-only; only the new output
directory is writable.

The controller must observe the exact Pod UID with every container terminated
before freezing the bounded, complete output tree and issuing its transport
proof. A worker-reported stopped flag is insufficient. Installed malformed
evidence is not automatically overwritten. Diagnostic preflight receipts do
not become business acceptance authority merely because the process exited
successfully.

The actual Kubernetes Pod factory admits the exact isolated acceptance module,
while other new module names remain forbidden. A rejection before any Pod POST
is classified from the original no-launch journal and cached separately. It
cannot mint a physical Pod-stop receipt or a trusted acceptance bundle.

Validator upgrades use a scope derived from the complete immutable runtime
pins, under one binding-wide lock. A bounded, no-link registry is persisted
before launch. Before a new runtime starts, the controller reconciles the
legacy binding scope and every indexed runtime scope, proves exact Pod stop
and absence, and removes only owned stale policies. Previous journals and pins
are preserved. Missing indexed authority, an untracked Pod or incomplete API
inventory blocks the new run. A binding-level attempt cache continues to
prevent automatic retries of stopped failed runs across image changes.

## Reading evidence and recording owner decisions

Only trusted stopped validator bundles may be installed. The reader rechecks
the full file roster, measured hashes, request identity, source binding,
nonce, image identity and subordinate reports. The programmatic projection
requires the complete gate roster, identified renderer and ordered final-PPTX
PNG roster. Stale, malformed or incomplete evidence fails closed. HTTP callers
cannot supply arbitrary certificate paths or model-authored passing flags.

The owner-scoped API is rooted at
`/api/v1/projects/{project_id}/versions/{version_id}/acceptance`:

- `GET` reads the current acceptance projection.
- `POST /records` appends human observations.
- `GET /rendered-slides/{number}/preview?receipt_sha256=...` reads a PNG bound
  to the current owner, source and receipt, with private/no-store caching.
- `POST /publication` records an explicit publication confirmation.

Publication requires `confirm=true`, an idempotency key, the expected
acceptance revision and the current locked source binding. Its record binds
the exact current visual record ID, PowerPoint record ID and programmatic
receipt hash. The UI requires a separate confirmation checkbox and disables
publication while human observation drafts are unsaved.

Human history is append-only. Revoking the latest conclusion does not restore
an older pass. A source change, changed or revoked evidence, incomplete human
observations, an active job or a pending gate blocks or revokes derived
publication authorization. Reading rendered images neither saves human
observations nor authorizes publication. Checking or publishing does not
request a new model run.

## Server regression evidence

The exact application source tested was
`3cee2b483112ed795de8689f72a0600bb0c4d86b`, obtained through Git. The isolated
Linux server operator passed **1004 tests across 25 stages**, with **zero
skips, failures or errors**.

Private receipt:
`/root/ppt-prepare-task-3cee2b483112-bc00d5ff9b-checks/summary.private.json`.

The existing 15 stages cover the worker host, candidate driver, task bounds,
connector, Agent API, PPT output contract, dispatcher/worker, review authority,
frontend domain/API, independent quality, quality diagnostics, needs-input,
planning recovery and site/controller packaging. Ten added stages cover:

- Human acceptance and publication, and trusted evidence reading.
- Coordinator, validator and stopped-output transport.
- Final-PPTX rendering, export equivalence and postflight regression.
- Frontend acceptance panel and acceptance client.

The transport stage also executes the complete real Kubernetes backend fixture
suite. It covers the exact entrypoint, real Pod intent/UID/stop/immutable-copy
path, old-image recovery, restart before launch, missing history, and registry
corruption/link/size boundaries.

These checks used synthetic fixtures. They did not mount or use production
database data or credentials, request a model or execute a paid engine task.
No local tests, compilation, builds or previews were run. Passing these tests
does not establish the outcome of a real delivered-deck preflight, runtime
rollout or desktop PowerPoint acceptance.

## Runtime build and rollout

Git-sourced three-image build passed. Private build receipt:
`/root/ppt-quality-3cee2b483112-build-20261101T165635Z-6c63981b/receipt.json`.
All images use `latest`, with source revision/context labels and immutable
deployment digests:

| Role | Registry repository | SHA256 digest |
| --- | --- | --- |
| Website | `arm-cluster-master:5000/ppt-service-site` | `76af0d75c6f81f08e655d9fbe8a5ae76a35ba45be5f0138b6a4b816ea822a2a4` |
| Controller | `arm-cluster-master:5000/ppt-agent-controller` | `98eec0aa0a3b9907c579c0e8938626ae11bff417e2397b973e95a51b4916ea4b` |
| Validator | `arm-cluster-master:5000/ppt-agent-validator` | `ac362a76a9756939421134248f06d5ec0734963005af15aeebc0513c0cfd3207` |

The Office addition preserves all 123 inherited filesystem layers and the old
validator runtime configuration. Office dependencies create layer 124; the
source COPY creates layer 125. Provenance labels are set before that final
COPY using build arguments, avoiding the legacy builder's maximum depth while
keeping the source context hash independent of its label value. No image was
flattened and no inherited runtime dependency base was silently replaced.

Guarded final rollout completed at `2026-11-01T17:01:35Z` (2026-11-02 01:01 CST).
Release receipt:
`/root/ppt-quality-site-controller-validator-3cee2b483112-20261101T165835Z-bd82ad7b/release-receipt.json`.
Both Deployments have one Ready replica. Actual controller, validator and
website backend/frontend source hashes and immutable digests were verified.
The coordinator was disabled during image switching and enabled only by a
final guarded controller rollout after runtime, protected-resource and public
authentication checks. `PPT_ACCEPTANCE_ENABLED=true` is verified in the actual
controller process and Pod environment.

Deployment and admission locks were released. Shared Agent/LLM configuration,
OAuth, feedback access, data/PVC/RBAC and protected resources were preserved.
No migration, scaling, business-row write or model task was performed. Public
checks returned ping 200, root 302 and unauthenticated protected APIs 401.

The earlier isolated preflight passed, but post-rollout observation exposed a
real Pod factory allowlist omission before any Pod POST. The transport masked
that rejection as missing stop evidence, leaving programmatic acceptance
pending. A guarded controller-only pause completed at
`2026-11-01T16:35:49Z`, preserving images, other Deployments and business rows;
receipt `/root/ppt-acceptance-pause-3d8e7a299d88-20261101T163436Z-3e8c9e28/pause-receipt.json`.
Commits `4fa75c9` and `3cee2b4` fix the exact entrypoint, prelaunch failure
classification and recovery across immutable runtime pins. The final release
separately binds the previously deployed validator `ee82c5eb...` and the
original dependency base `e25951c7...`; it does not overlay the already
125-layer deployed image or rewrite its old execution journal.

## Exact delivered-deck preflight

The isolated no-model preflight completed successfully. Its private receipt is
`/root/ppt-quality-revalidation-20261101T155337Z-57d12df4/receipt.private.json`;
its separate programmatic receipt is `acceptance-receipt.private.json` in the
same directory. Request nonce/evidence ID:
`8e6c79dc-e0d8-417b-ad7c-c0caf8a51bf0`.

The source is project `c9d480b5-be18-46ed-839b-e65a9bd57eda`, Version
`b6b02806-506a-414e-a314-8d3ec230f9f2`, generation job
`f5d0d14e-df23-48ea-b116-4737281abd75`, frozen fence-directory `42`.
Manifest SHA256:
`838cb951bfa88bd04f865e8b223e9caab99438aacfec334d77bbb7308a6eea29`.
Delivered PPTX SHA256:
`19d9e26451ebb621f4cfa9452edaa7b5381c2a075d0c26bbf587933937e3207d`.

The trusted original request was reconstructed from the read-only outbox task
and deployed chart oracle in a verified PostgreSQL read-only snapshot. It is
not presented as a preserved historical request. The fresh acceptance binding
SHA256 is
`70a6d55c71d310eeeeb5d9c5c0ab4df9c2421660445a3f66e9760a16955de2b1`.

Actual validator exit was zero, all seven programmatic gates passed, required
pending programmatic gates were empty, and request/report binding was verified.
Complete candidate inventory equality was checked after execution. Exact Pod
stop and deletion of this operation's Pod and deny policy were confirmed;
cleanup completed without error. Raw final-PPTX, postflight and equivalence
reports remain bound by their descriptor hashes in the private receipt.

The run used the immutable validator built from Git commit `3d8e7a2` and made
zero model requests or business-row writes. Its receipt retains
`publication_authorized=false`, `human_visual_acceptance=false`,
`powerpoint_acceptance="pending"` and
`full_exporter_postflight_verified=false`. It proves fresh export equivalence,
without rewriting historical exporter provenance. This diagnostic run did not
install business acceptance evidence, create a Version or publish the deck.

## Installed evidence and deployed acceptance projection

The post-rollout read-only audit completed at `2026-11-01T17:05:32Z`
(2026-11-02 01:05:32 CST) against application source
`3cee2b483112ed795de8689f72a0600bb0c4d86b`. Private raw report:
`/root/ppt-acceptance-audit-20261101T170532Z-bcc91cba-86b5-4f5a-b39f-8cf3ef9d9096/audit.private.json`.

This observation verifies the controller-installed business acceptance bundle,
separately from the earlier isolated diagnostic preflight. The audit used the
deployed resolver's source/provenance resolution, the same trusted immutable
bundle reader used by the API, the actual acceptance-store history reader and
the normal acceptance projection. It retained ownership and stopped-job
prerequisites while replacing the API wrapper's row-lock acquisition with an
explicit PostgreSQL **REPEATABLE READ READ ONLY** transaction. The observed
controller identity remained unchanged throughout the audit.

The installed receipt SHA256 is
`1fbdcdbd20669c65c82d4db9fe397439485656ff439a0260e0a7de89a1aee8d4`.
The reader verified its complete retained file roster, source and request
bindings, exact validator Pod stop/transport proof, report hashes and final
PNG roster. The programmatic status is `passed`, with all seven gates passed
and `pending_gates=[]`:

- `independent_raster_regeneration_and_binding`.
- `independent_delivery_audit`.
- `independent_native_ooxml_and_chart_data`.
- `independent_postflight_source_and_quality`.
- `independent_export_build_contract`.
- `independent_postflight_and_delivery`.
- `independent_final_pptx_rendering`.

The actual final-PPTX render contains **10 PNG pages, each 1280 × 720**. Page
roster SHA256:
`5d82ecd243ae550b198a3e5a4f7438648d33d16272daa5bb828f79a2ea1418a9`.
Native structural counts are 10 slides, 220 shapes, 147 text shapes, 10 notes,
zero charts, zero pictures and zero semantic connectors. These are measured
structural facts and renderer results, not desktop editing observations.

The original delivered PPTX remains unchanged at SHA256
`19d9e26451ebb621f4cfa9452edaa7b5381c2a075d0c26bbf587933937e3207d`.
The source revision is **26** and the acceptance revision remains **2**. The
original human records were read and preserved:

| Record ID prefix | Acceptance revision | Current observation |
| --- | --- | --- |
| `9453bb74` | 1 | Visual review passed; current and not revoked |
| `98f59ed4` | 2 | Desktop PowerPoint checks pending; native-chart editing not applicable because the deck has no charts |

The projected acceptance status remains `pending`, with exactly
`powerpoint_review_pending` remaining. `can_publish=false`, publication status
is `draft` and `publication_authorized=false`. Reading and verifying the new
bundle did not append or alter human observations, authorize publication or
rewrite the earlier validator receipt. The existing visual record continues
to describe the original source-SVG previews; it is not converted into a human
observation of the newly rendered final-PPTX pages.

The audit issued no write SQL or row-lock statements and performed no business
writes, model tasks or publication commands. Raw report data stays in the
server's private evidence directory. This is a server-side evidence/projection
verification: an authenticated browser/UI check was not performed after this
rollout, because the in-app browser had no active page at the time.

## Remaining owner acceptance

Desktop Microsoft PowerPoint open → edit → save → reopen remains pending.
The owner must record the actual environment and observations against the
current delivered Version. The owner should also inspect the final-PPTX
rendered pages; existing visual records bind the source-SVG previews and are
not rewritten as observations of the new rendered pages.

Formal publication remains pending until all current programmatic and human
requirements pass and the owner separately confirms publication. This record
does not claim final publication, OpenSpec completion or archive.
