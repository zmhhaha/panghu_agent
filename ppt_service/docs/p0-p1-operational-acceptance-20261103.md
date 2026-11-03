# PPT P0/P1 operational acceptance — 2026-11-03

Scope: `complete-ppt-p0-p1-operational-acceptance`, related parent R1–R9 and TL7.
Formal owner publication (R10), usage/fees and eligibility are excluded. All
application tests, builds, parsing, PPTX exports and rendering run on the ARM64
server. The separately requested desktop gate uses real Microsoft PowerPoint on
the Windows desktop, with a synthetic working copy. No production business row
is written by an administrator and no browser credential is extracted.

## Implemented and verified

- Source upload capacity admission: preserve 128 MiB by default; measure the
  destination filesystem before creating the new UUID directory and before each
  actual unbuffered write. Handle short writes and ENOSPC, clean only this upload,
  fail closed on unavailable capacity measurements, preserve existing read,
  download and deletion. See `source-upload-capacity.md`. This is server disk
  protection, not a per-user usage or entitlement policy.
- Finite shared catalog governance: bounded historical bindings/pools/bundles/
  environments, strict byte budgets and exact-consumer compatibility proofs,
  immutable historical host/policy/UID references and protected non-PPT routes.
  Missing consumer proof retains the previous admission bound. See
  `../../agent_service/docs/CATALOG_CAPACITY_20261103.md`.
- Reproducible current-schema multi-user, parser, recovery and backup acceptance
  runners. They use Git-frozen source, synthetic identities and disposable
  resources, with retained failed runs and positive cleanup evidence.

## Server receipts

| Scenario | Exact evidence | Result and scope |
| --- | --- | --- |
| Full regression at `6f6b5ac769ddb1db1b71751f0fec582985d9aa2a` | `/root/ppt-prepare-task-6f6b5ac769dd-ab3a82359e-checks/summary.private.json` | 32 stages, 1,276 tests, zero skipped/failed/errors; actual native exporter and Office/Fitz rendering included. No models or production database. |
| Current security/parser/operations at `1df6b8a71804e5c92e891c74079ff5176f729269` | `/root/ppt-p0p1-security-1df6b8a-r4/evidence/status.json` | 472 tests, zero skipped/failed; real RS256 synthetic JWTs, ASGI TestClient, independent PostgreSQL and files. No real browser, Casdoor session or listening HTTP socket. |
| Kernel faults at `367af49` | `/root/ppt-p0p1-faults-367af49-r4/summary.json` | SIGKILL, cgroup OOM, absolute timeout with detached child, tmpfs ENOSPC and network/privilege denials passed; owned Docker resources removed, retained synthetic input unchanged. Not an Agent workflow or Kubernetes recovery drill. |
| Schema-4 consistent restore at `c3fa925` | `/root/ppt-p0p1-backup-c3fa925-r1/evidence/status.json` | Real pg_dump/pg_restore, all 15 tables, file bytes/metadata, append-only history and runtime privileges matched. Disposable, quiescent synthetic source; not a production/PVC/offsite recovery claim. |
| Complex editable content at `d446fd4` | `/root/ppt-p0p1-complex-d446fd4-r2/receipt.json` | Initial and revised six-page synthetic decks, actual native exporter and final PPTX-derived renders. Long Chinese text, PNG, editable data chart, embedded XLSX and speaker notes checked. Not an owner Agent generation or publication. |

The 472-test matrix includes 26 existing signed-JWT API tests, seven current
review/concurrency tests, 178 execution-isolation contracts, 135 operations/parser
tests, 78 catalog tests and 48 domain/connector recovery tests. Some execution and
recovery collaborators are explicit test doubles; those results do not prove a
production Worker crash or a real SSO/Ingress flow.

Real parser fixtures cover Markdown, DOCX, text-layer PDF and PNG/JPEG, the actual
150/151-page PDF and 500,000/500,001-character Markdown boundaries, encrypted/
attached/form/scanned/corrupt PDF, Office active/encrypted/external parts, archive
entries/expansion/paths/links, XML depth/nodes/entities, image dimensions/pixels,
source hash/provenance, hardlinks and inventory traversal. Two aggregate-budget
tests explicitly reduce constants; parser process deadlines remain a separate
sandbox/control boundary.

The OOM probe requires the private cgroup `memory.max=67108864`, child SIGKILL and
kernel `memory.events` increments (`oom: 0→1`, `oom_kill: 0→1`). Docker's
`OOMKilled` flag alone was false when a child was selected. Earlier failed probes
under `c3fa925-r1`, `d446fd4-r2` and `6f6b5ac-r3` remain failed evidence. They are
not relabelled as passing.

## Actual Microsoft PowerPoint

All twelve retained final-PPTX-derived PNGs (initial/revised, pages 1–6) were
actually viewed by Codex, with no observed blocking clipping or overlap. Page 2's
eight Chinese lines are complete and separated; page 3's arrows connect only
adjacent ordered cards and avoid labels; page 4 shows A/B/C and 12/18/25
consistently; page 5 preserves the synthetic bitmap and its separate editable
explanations. Revised page 3 adds spacing and page 6 explicitly describes the
limited revision. Per-page image hashes and observations are retained in
`D:\github\armbianbegin\.research-temp\ppt-p0p1-visual-20261103\visual-review.json`.
This is an agent visual review of a synthetic exporter fixture. It does not
create a signed human/owner review record in the website or replace TL7/R8.

The retained revised synthetic source is 43,869 bytes, SHA256
`88622f30db49dcc4550be4f52f12e82b271834b2e751a4ea1b5c6422ead8bfc5`.
Real PowerPoint 16.0 ran through COM in desktop session 1. On an independent
working copy, it edited a native text frame and the single native chart's first
embedded-workbook value from 12 to 13, saved a new PPTX and reopened it. All six
pages, changed text and workbook value 13 were observed after reopening, and the
original source hash remained unchanged.

Receipt:
`D:\github\armbianbegin\.research-temp\ppt-p0p1-office-edit-20261103-r2\receipt.json`.
Saved PPTX:
`D:\github\armbianbegin\.research-temp\ppt-p0p1-office-edit-20261103-r2\synthetic-office-edited.pptx`,
SHA256 `02095560e27d7298e9685011acfaee3f70e04d8bfad102aeba0cad4173b2bcee`.
Operator:
`D:\github\armbianbegin\.research-temp\ppt-p0p1-office-20261103.ps1`.

The first attempt saved successfully but failed when reactivating chart data in
a read-only reopened presentation with “Class not registered”; its failed
receipt is retained as `ppt-p0p1-office-edit-20261103-r1/receipt.json`. The second
attempt reopens its owned working copy writable and verifies both changes. No
Office installation, registry, security settings or original deck was changed.
This is actual Office automation; it is not a human visual acceptance statement
or acceptance/publication of a production owner Version.

## Outstanding external acceptance

The in-app browser tool fails before page access with Windows error 3:
`failed to write kernel assets: 系统找不到指定的路径。 (os error 3)`.
Reset and reconnect returned the same error. No fallback reads cookies or
impersonates an owner. Therefore TL7's normal-owner DSH ten-page revision,
authenticated refresh/timing/history checks and real two-account browser/SSO
matrix remain pending. The retained baseline and prepared owner feedback are
unchanged. A synthetic complex sample does not complete those owner gates.

Full production Agent/Kubernetes recovery and consistent production/PVC/offsite
backup evidence remain separate from the isolated contract, kernel and restore
receipts above. No parent R1–R10 is marked complete merely because one component
is verified. No OpenSpec apply/archive or formal owner publication is performed.

## Release and final-render follow-up

Shared-control compatibility rollout, site-only upload-protection publication,
the 12-page visual review and any disposable Kubernetes probe are recorded below
when their exact receipts are available. The statements above do not assert that
those actions have already occurred.
