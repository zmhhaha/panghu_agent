# PPT Agent integration: phase closeout, 2026-10-30

Production cutover is **not complete**. The owner requested "切换", then asked to
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
