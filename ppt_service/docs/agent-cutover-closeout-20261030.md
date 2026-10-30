# PPT Agent integration: phase closeout, 2026-10-30

Latest status: **production runtime cutover completed**; full PPT domain
acceptance remains pending. See the completed continuation below.

## Earlier phase closeout (preserved history)

At that earlier stopping point, production cutover was **not complete**. The owner requested "切换", then asked to
close this phase for rest before the remaining deployment work finished. Preserve
the original generation failures and incomplete domain acceptance.

## Committed work and checks

- Application `4630fce823881ff3bf12bf97d5ceb916c0c55a44`, outer
  `aaa1745689e6367066979f60e5fb37994de8521d`: independent administrative runtime
  preflight protocol and tests, committed, pushed and fetched through Git into
  `/root/ppt-runtime-preflight-4630fce-20261029/panghu_agent`.
- Server-only isolated checks: **184 passed, zero skips/failures/errors**;
  `/root/ppt-runtime-4630fce82388-c5647986-checks/receipt.json`. These test the
  protocol, acceptance probe, runtime boundaries, worker host and registry. They
  do not prove actual runtime/model or PPT business acceptance.
- An earlier check runner failed before pytest could start because its exported
  source root was not readable by the container user. The operator permissions
  were corrected before the passing run; no application test was changed.
- The new protocol uses an explicitly administrative schema, one fixed model
  echo, actual lease expiry and HTTP401, scoped stop evidence and separate
  external Pod observations. It creates no PPT result, confirmation, checkpoint,
  generated artifact or production acceptance.
- Reviewed runtime-only approval/publication, cutover and closeout helpers are
  retained locally under `.research-temp/`. The installed r5 set is at
  `/root/agent-service-deploy-20261026/ppt-agent-operators-20261029-r5` with
  `installed.json` hashes. They have not completed real runtime acceptance.

## Attempt and safe stop

The first real preflight stopped during resource setup, before a manifest, Pod or
model request was created. Its root cause has not been established. Independent
offline rendering subsequently succeeded; this does not prove the resource setup
or runtime checks passed. The runner's failure path also needs improvement: it
retains the lock on its in-memory resource list and hides the original error
until recovery. Capture the original error and add ownership-checked resource
reconciliation before trying again.

The attempt is retained at
`/root/agent-service-deploy-20261026/ppt-runtime-preflights-4630fce-r1`.
`operator-closeout.json` proves the named resources are absent, no execution Pod
or model request occurred, and the workflow lock was released. The allocated
three-UID block stays permanently reserved and must not be reused.

During this maintenance window another deployment changed the shared Agent
dispatcher and resumed it. The old restore helper correctly refused the changed
template. Closeout observed the same dispatcher UID, a newer generation, one
ready/updated replica and the new template. It retained that deployment, recorded
`superseded.json`, stopped only this attempt's driver and released its lock. It
did not restore the old shared configuration. Observed control image:
`sha256:d14ea800d25b0e4a930ca1f1b295c109610aa8559c8b7f5827ba839d9815b197`;
config `agent-service-config-e2ae9c3cae730e14ff29`.

No production schema migration, PPT backend change, project update or artifact
publication was performed in this phase. The native PPT backend remains the
last verified configuration. No production database rows were edited.

## Resume from here

1. Re-read live shared Agent sources and compare them with the intended images.
   The old f92 control image cannot overwrite the newer shared deployment without
   reviewing and retaining its research changes. Current source compatibility
   has **not** been established.
2. Repair the preflight operator's setup diagnostics and safe cleanup handling;
   use a fresh state directory, bootstrap identities and UID block. Run the five
   real runtime checks and preserve the exact Git/image/evidence bindings.
3. Prepare and review an additive runtime-only catalog against a fresh snapshot.
   Keep `domain_suite_passed=false` and `domain_workflow_verified=false`, preserve
   all existing research routes/pins and normal artifact/review/publication gates.
4. Only then perform the official drained, backed-up schema4/backend cutover and
   actual controller/API/auth/readiness checks. Final records must distinguish a
   successful infrastructure switch from full domain workflow acceptance.
5. Complete real generation/revision, review/download and PowerPoint acceptance;
   the two upstream generation stream failures remain unresolved. See
   `agent-integration-20261029.md` for the retained original evidence.

OpenSpec project: `ec663f5a-53d3-4a0e-9037-933eb788d183`, change
`add-hermes-ppt-workbench`. The change stays open; PA4 stays incomplete.

## Completed runtime cutover continuation, 2026-10-30

### Verified before production promotion

- Application `e9ad9aac460e0eb39c086b532f0ee4343c85c063`, outer
  `ac60decc39021e9a1eb17905f9b8855a901d0b92`, committed/pushed and fetched through
  Git into `/root/ppt-agent-cutover-e9ad9aa-20261030/panghu_agent`. The scientific
  workflow commit chain was merged into this source.
- Server-only checks: **1160 passed, zero skips/failures/errors**, with frontend
  TypeScript and Vite build successful. Receipt:
  `/root/ppt-agent-e9ad9aac460e-22c5783cd8-checks/receipt.json`.
  An additional Hermes image-label check passed separately. No local tests,
  builds or previews were run.
- All five application images were built and pushed with `latest`, then bound
  using `latest@sha256`. OCI revision/source/context labels and actual image
  identities were verified. Build receipt:
  `/root/ppt-agent-e9ad9aac460e-build-20261030T054828Z/receipt.json`.
- The 93 image-owned files measured from the existing production Agent control
  matched reviewed Git content `f55b8c2ae7c6b5ddf08a6dd688d07b76f2332fa9` byte for
  byte. The target retains scientific runtime code. This is source-content
  evidence, not a claim that the older image had unavailable OCI Git labels.
  Evidence: `/root/agent-service-deploy-20261026/ppt-live-full-source-measurement-20261030.json`.
- Operator cleanup now records original sanitized errors and reconciles only
  resources with the captured owner and Kubernetes UID. It proves deletion and
  empty UID scopes before restoring the dispatcher and releasing the workflow
  lock. A bounded phase-transition retry re-runs the original idle gates only
  for the explicit transient `production_execution_not_idle` result. The 13
  operator checks passed on the server. User work is not cancelled.
- All five **PPT v2 runtime prerequisites** passed against the exact final
  images/catalog: Pi package/capabilities, one fixed model echo, isolation,
  actual expired-grant HTTP401, scoped stop evidence and external Pod cleanup.
  Evidence set:
  `/root/agent-service-deploy-20261026/ppt-runtime-preflights-e9ad9aa-r6/runtime-preflight-set.json`.
  The final cleanup receipt reports success, resources deleted, UID scopes empty,
  original dispatcher replicas restored and shared lock released after proof.
- The earlier r4 set used default PPT v1 contracts and is excluded from v2
  release evidence. The r5 v2 run passed its first profile, then stopped on an
  idle observation; its cleanup/restoration receipts remain retained.

| Image | Digest bound to `latest` |
| --- | --- |
| `ppt-service-site` | `sha256:f1347456e29424c3bc92d90b33a9fa7c57a86e32c6a2672b8503482e4c9d5255` |
| `ppt-agent-controller` | `sha256:f025ec1482e79200a94be7c2199b78b84e6875d288909fecfa1da99089757b08` |
| `ppt-agent-validator` | `sha256:01039dbf8076756d1bc12e3a1a4b346b142d457662f5178b9c72cb50b0ac6bed` |
| `agent-service-control` | `sha256:956a42131a23e9443774584292cd15cdc72811f8e782d0d35db283dfee791c03` |
| `agent-service-pi` | `sha256:1903341a60619849472795bd4e189fb083ae991a4a89f7ea9611e24b983f03ed` |

Pi source remains `https://github.com/zmhhaha/pi-cage`, revision
`e0262e65e43c6924164e995394f715dc5d19c210`, Pi version `0.99.2`.

### Completed production outcome

The runtime-only production cutover completed at 2026-10-30T07:34:06Z.
`PPT_EXECUTION_BACKEND=agent-service`, admission enabled, schema4; site and
controller each have one ready/updated replica. Controller HTTP capabilities
confirm all five Pi v2 profiles ready. Pi remains the user's pi-cage.

- Reviewed additive publication:
  `/root/agent-service-deploy-20261026/ppt-runtime-promotion-e9ad9aa-r2`.
  It added five PPT hosts and preserved historical registry/routes/pins.
  UID range `45000000..45065535` is permanently reserved. The first plan stopped
  because `44000000` had a prior video reservation; nothing was overwritten.
- Cutover operation `9b310c27-a458-470e-8109-d828cdd4ff40`:
  `/root/agent-service-deploy-20261026/ppt-production-cutover-e9ad9aa-r1`.
  Native jobs/slots/parser/outbox drained to zero, the old controller exited,
  and the official schema3-to4 migration/runtime grants passed.
- Private backup `ppt_service-before-schema4.dump`: 681836 bytes,
  SHA256 `e5462c894589b44e74a5b9e1d3a715b375bd5f8c429656e715e01c4317dc015d`.
  Its custom-format inventory was checked; automatic restore is prohibited.
- The copied legacy site readiness command still required schema3. The operator
  repaired only that known command using the exact official Git template, whose
  probe reads the migration REVISION. Three server regression checks passed;
  readiness and the subsequent rollout passed. Application sources were not
  changed to bypass readiness.
- The unchanged OAuth sidecar has two local Docker manifest aliases. On its
  actual Kubernetes node, both immutable references and the running container
  resolved to the same Docker config ID. Eight server checks cover exact
  application digests, alias proof and audited node/SSH boundaries. No Casdoor,
  OAuth settings or image reference was changed.
- A concurrent research-only publication advanced the shared catalog from
  `372ffa0225729b982dfc7cb4ed5838541b9ba0225b575e305305d8e769662de0` to
  `a9dac488b1f071db16315d5d40bd16721fc48cdb4e7e8aa63f67c9f83c54b430`.
  Exact scoped review verified five research route revision changes, five new
  bindings/pools, two referenced tool environments and five old pool disables.
  All PPT routes, bindings, 15 historical/current pools and 15 immutable host
  maps stayed byte-identical. Eight comparator checks passed on the server.
  The newer research publication was retained.
- Final `runtime-release-check.json` passed at 2026-10-30T07:33:28Z;
  SHA256 `b2502a07025b04844846ebf4b946dbac3b171934537001ca5170a9e9e789fded`.
  It verifies actual container imageIDs, live site socket, controller/API path,
  schema4/runtime privileges, append-only review permissions, original PVC/DB
  reference/settings and public GET `/`302, `/api/v1/projects`401, `/ping`200.
  No business rows were read or written and no model job was submitted by this
  final check. `finish` attached the reviewed receipt, recorded completed=true,
  and removed the owned deployment lock; absence was independently confirmed.
- Final installed operator set:
  `/root/agent-service-deploy-20261026/ppt-agent-operators-20261030-r15`,
  with immutable `installed.json` file hashes. All application source transport,
  checks and image builds used Git and the server; operator scripts alone were
  transmitted over SSH.

Retain the explicit owner authorization scope:
`runtime_prerequisites_only`, `domain_suite_passed=false`,
`domain_workflow_verified=false`. A successful infrastructure cutover must not be
described as full PPT domain acceptance. Preserve production storage/auth,
scientific routes/pins, deadlines/cancellation/idempotency and independent
artifact, review and publication gates. Do not edit business rows or Casdoor.

### Still incomplete

Earlier full-domain trials passed initial/options/prepare but generation failed
twice with an upstream HTTP200 SSE error, “Responses stream reported a failure”.
Revision was not submitted; real generation/revision, review/download and
PowerPoint acceptance remain pending. Preserve those failed records and do not
substitute the fixed echo checks for them.

OpenSpec project `ec663f5a-53d3-4a0e-9037-933eb788d183`, change
`add-hermes-ppt-workbench`, remains open. PA4 remains incomplete until actual full
domain workflow acceptance is recorded.
