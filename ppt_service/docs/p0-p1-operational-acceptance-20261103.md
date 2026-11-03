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
| Full regression at `6f6b5ac769ddb1db1b71751f0fec582985d9aa2a` | `/root/ppt-prepare-task-6f6b5ac769dd-ab3a82359e-checks/summary.private.json` | 32 stages, 1,276 tests, zero skipped/failed/errors; actual native exporter and LibreOffice/Fitz rendering included. No models or production database. |
| Current security/parser/operations at `1df6b8a71804e5c92e891c74079ff5176f729269` | `/root/ppt-p0p1-security-1df6b8a-r4/evidence/status.json` | 472 tests, zero skipped/failed; real RS256 synthetic JWTs, ASGI TestClient, independent PostgreSQL and files. No real browser, Casdoor session or listening HTTP socket. |
| Kernel faults at `367af49` | `/root/ppt-p0p1-faults-367af49-r4/summary.json` | SIGKILL, cgroup OOM, absolute timeout with detached child, tmpfs ENOSPC and network/privilege denials passed; owned Docker resources removed, retained synthetic input unchanged. Not an Agent workflow or Kubernetes recovery drill. |
| Real disposable Kubernetes at `9c68792` | `/root/ppt-p0p1-kubernetes-9c68792-r2/status.json` | Three synthetic Pods; actual non-root/caps/NNP/seccomp, read-only root/input, no SA token, independent memory outputs, TCP ingress/egress allow→deny→restore on the same Pod UIDs/IPs, child SIGTERM/reap and owned namespace cleanup passed. Not a production Worker/Agent/SA admission or full recovery matrix. |
| Schema-4 consistent restore at `c3fa925` | `/root/ppt-p0p1-backup-c3fa925-r1/evidence/status.json` | Real pg_dump/pg_restore, all 15 tables, file bytes/metadata, append-only history and runtime privileges matched. Disposable, quiescent synthetic source; not a production/PVC/offsite recovery claim. |
| Complex editable content at `d446fd4` | `/root/ppt-p0p1-complex-d446fd4-r2/receipt.json` | Initial and revised six-page synthetic decks, actual native exporter and final PPTX-derived renders. Long Chinese text, PNG, editable data chart, embedded XLSX and speaker notes checked. Not an owner Agent generation or publication. |
| Real final-validator rejection/recovery at `d34bd8d` | `/root/ppt-p0p1-final-failure-d34bd8d-r3/receipt.json` | Actual rendered glyph collision rejected; healthy follow-up passed; fixed-tool unavailability rejected; a separate healthy container passed. All three containers stopped and removed, old PPTX/PDF/layout/six previews unchanged. Git-mounted application code and image-owned tools, not a complete PPT business recovery workflow. |
| Real joint Agent recovery at `791a1d0` | `/root/ppt-p0p1-joint-recovery-791a1d0-r3/joint-recovery.json` and `cleanup-reconciled-20261103.json` | All eight actual Agent/API/PostgreSQL/Dispatcher/Kubernetes/WorkerHost scenarios passed. Original runner overall status stays false because final helper cleanup CAS failed; supplemental exact-owned cleanup passed and namespace is absent. Synthetic Python payload, no Pi/Hermes/model or production PPT Version. |

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

## Shared-control release

The minimal shared-control overlay was published and all three actual Pods
(API, model proxy and dispatcher) were verified Ready with immutable image
`arm-cluster-master:5000/agent-service-control:latest@sha256:7b8e4c4b8b6a620adb9842dbfa311b49de9be684e145e9892e4520e2308a3717`.
Source revision is `c3fa92568f73256bfdbcb2655f75cded4143f42d`.
The single-lock operator required idle state, stopped the dispatcher, performed
full-spec/UID/resourceVersion CAS changes, bound the publication handoff and
restored the original replicas. Complete evidence is
`/root/agent-service-deploy-20261026/ppt-p0p1-control-capacity-20261103-d446fd4-r1/completed.json`.

The original 95 bindings/pools, immutable CM
`agent-service-config-f78661ea19a3a6be3c57`, all routes, five shared slots, PPT's
business-slot policy and UID configuration were preserved. Public configuration
SHA256 is `42705a0bec6d3ac95c1191306d0d2e9993b56db561c5b6bbac5d04e8c3b2c978`;
old policy pins SHA256 is
`c9336e15c61d98fd2eaf090abbc6f5c3b49131f9f2429ab4a37a523aaba9b042`.
The exact image-owned reader verified all 95 retained hosts, synthetic 105/256
acceptance and 257/unknown-field rejection, plus old-reader rejection at 105.
The new image changes only three capacity-related files over the original
donor; complete rootfs/permissions and runtime configuration were compared.

This rollout upgrades the three control readers. A future Worker/publication
that crosses 100 still needs that exact candidate-worker compatibility proof;
missing four-role proof retains the legacy admission bound. Historical bindings
were not pruned and no permanent UID was reused.

The first Kubernetes probe was rejected by its own
finite ConfigMap quota because Kubernetes had injected `kube-root-ca.crt`.
That failed receipt remains under `ppt-p0p1-kubernetes-3154860-r1`; its owned
namespace was cleaned. The corrected allowance is two ConfigMaps (one public
automatic CA, one synthetic input), without tokens or Secrets, and r2 passed.

## Site-only release and public checks

The upload-protection site was built from an independent server-local Git clone
at the exact tested application revision `6f6b5ac769ddb1db1b71751f0fec582985d9aa2a`.
Build receipt: `/root/ppt-p0p1-site-build-20261103-r2/receipt.json`.
The concrete plan and its reviewed JSON patch are retained at
`/root/ppt-p0p1-site-plan-20261103-r2/`. The only mutations were the site container
image and pod-template provenance annotations. The running image is
`arm-cluster-master:5000/ppt-service-site:latest@sha256:2491b67b6ace1d66cacb4426b7f49f5c2bb5a29f8dc8d93f3bf22f84c55a9bfc`.

Successful apply receipt:
`/root/ppt-p0p1-site-plan-20261103-r2/apply-9d64ad5f93b94368a905357d9689150d/release-receipt.json`.
It confirms the Ready Pod's immutable image and installed source/frontend
hashes, unchanged protected configuration, no controller/validator/Worker
changes, no migrations or business-row writes, and released admission guard
and deployment lock. No model job was submitted. Anonymous public checks after
rollout returned HTTP 302 for `/` and HTTP 401 for `/api/projects`; these verify
entry-point availability and authentication enforcement, not a signed-in user
workflow.

The earlier successful r1 build remains retained. Its first plan stopped before
deployment because the finite ConfigMap-list read budget was smaller than the
actual retained historical catalog. The corrected operator raises only that
specific list budget from 8 MiB to 32 MiB, keeps other reads bounded at 8 MiB,
and binds its own exact hashes in the separate r2 build and successful plan.
Failed and passing evidence directories were not overwritten.

## Actual final-validator rejection and recovery

The independent synthetic baseline was mounted read-only. The quality probe
changed only two text frames in a separate candidate's first slide, preserving
all other native package parts. The real LibreOffice/Poppler/Fitz pipeline
reported the exact two-shape `text_collision`, returned
`final_pptx_text_layout_failed`, and created no successful final-render receipt
for that bad candidate. It then rendered the original healthy baseline
successfully in a fresh output directory.

A second container mounted an administrator-owned non-executable file
read-only over `/usr/bin/pdfinfo`. The original validator rejected the actually
unavailable fixed tool with `final_pptx_renderer_unavailable`. A third container
without that fault rendered the healthy baseline successfully. This is a real
tool-availability rejection, not a claim about every mid-render failure mode.

Exact Git source `d34bd8d4c8b9df5cf3fe8e20bc2938d31ad9b98c` was loaded explicitly
from `/src` read-only, while tools/dependencies came from immutable validator
`latest@sha256:4492a878db0d575f20542379a61986500a1c5263b5ecc5460026a87937549bc9`.
The enclosing operator records this mixed code/tool origin rather than claiming
image-owned application imports. All capsules had no network, read-only root,
UID/GID 10000, no capabilities, NNP and finite CPU/memory/PID/tmpfs budgets.
The image's declared `/opt/data` volume was explicitly replaced with a tiny
read-only tmpfs; no anonymous volume was allowed. Each actual image identity,
exit status, PID-zero stop, exact-ID removal and absence was verified.

The original baseline bytes, PDF, text-layout diagnostics and all six preview
hashes stayed unchanged throughout. The first attempt was stopped before
execution because Docker had created the image-declared anonymous volume; its
three image-populated HOME files and exact owned volume were subsequently
recorded and removed in
`/root/ppt-p0p1-final-failure-d34bd8d-r1/anonymous-volume-cleanup-20261103.json`.
The second attempt passed quality rejection but exposed the administrator stub's
umask-restricted read permission. Both failed attempts remain separate; r3 fixes
the stub's exact 0444 mode and passes all three cases. No production image,
business Version, owner review or model task was changed.

## Actual joint Agent recovery and reconciled cleanup

Git-frozen source `791a1d0798c43cd0efb03c5bdc7d6fd446af1a6b` exercised the real
Agent API, PostgreSQL Store, a separate Dispatcher process, Kubernetes provider,
RemoteWorkerAdapter, WorkerHost journal/lease watchdog and Linux UID stopper.
The eight scenarios were baseline artifact/download, dispatcher SIGKILL/reap and
recovery of the same execution, exact-UID Worker interruption, absolute deadline,
real control-to-Worker network denial and lease expiry, actual WorkerHost output
schema rejection, tmpfs ENOSPC and a successful following job. Each retained one
durable attempt/allocation; post-baseline cases retained the same downloadable
baseline artifact bytes/hash. Restart also checked the same request, fence,
Pod UID and native nonce and one artifact PUT. Stop observation preceded slot
release. No model request occurred.

Evidence directory:
`/root/ppt-p0p1-joint-recovery-791a1d0-r3`.
`joint-recovery.json` has `phase=complete`, `passed=true`, eight scenarios.
The original `status.json.passed=false` remains unchanged: only final trusted
PostgreSQL helper cleanup hit a resourceVersion/CAS conflict after both helper
containers had positively terminated. Supplemental exact-UID/fresh-RV cleanup
removed the stopped helper, verified zero Pods and deleted the exact owned
namespace; `cleanup-reconciled-20261103.json` is passing. It retains SHA256 of
the original status, cleanup and joint receipts. The first supplemental operator
stopped after removing PostgreSQL because an empty kubectl list decoded as null;
the next read verified zero Pods before namespace deletion. Neither failure was
rewritten as a passing original run.

Exact images were control `latest@sha256:7b8e4c4b8b6a620adb9842dbfa311b49de9be684e145e9892e4520e2308a3717`,
Worker `latest@sha256:8a3e77253c7f2f75c998b22adef7e0151ca7e2797ac5d341b782e8426dd75a21`
and acceptance PostgreSQL `latest@sha256:f586fed48e5b6f937c746a090170e8be142285906b245cf961416d99b22a11fa`.
The control image has one explicitly pinned historical `worker_entry.py` blob
`0500563e0117570964089a9b5e8ba135c78c3224`, SHA256
`993f2b8dffccb698291c907a3bcb7bc840fb0c49b669fd46f0735325c94b2782`.
The exact difference from current Git is the reviewed four-line PPT
`max_processes=128` block; HostConfig/host_policy are unchanged. Other selected
control files and all selected Worker files match current Git. `source.json`
records per-role hashes and `all_selected_image_sources_from_current_commit=false`.
Production control was not rebuilt to hide this lineage.

Two earlier failed attempts remain separate: r1 could not pull the initial
PostgreSQL digest; r2 rejected the historical control-source mismatch. Their
owned namespaces were removed with separate reconciliation evidence. All three
32-UID ranges remain permanently reserved; no reservation marker was removed.
No real node failure, production PVC/offsite restore, production AppArmor/input
mount matrix or Pi/Hermes business execution is claimed. The joint recovery and
separate real final-validator probes complete C8's scoped component evidence;
parent R5 and normal owner-deck checks remain pending.
