# Review-draft revisions and reported Agent usage

This change connects terminal Agent accounting to the existing PPT ledger and
exposes separate task and account summaries. It also validates an actual
single-page revision of the existing ten-page DSH review draft. A clone canary
is distinct from a production browser submission or human acceptance.

## Accounting contract

Only authenticated terminal `GET /v1/jobs/{id}` measurements are recorded after
matching the remote job, business reference, frozen pins, attempt/fence and
physical stop. The remote execution identity is frozen with its accounting
payload. Repeated observation cannot insert a second receipt or replace an
earlier frozen measurement. Later artifact rejection, cancellation or
needs-input mapping preserves already observed accounting.

`/api/v1/jobs/{id}` returns task usage. `/api/v1/me` returns account usage and
the existing generation/revision counters, configured limits and remaining
allowance. These projections preserve issuer/subject ownership. They do not
change admission, reservation, reset, refund or quota policy, migrate data, or
backfill historical executions.

Missing counters remain unknown. Observed Pi tokens without an explicit
completeness assertion remain partial. Missing supplier cost remains unknown,
never an estimated zero. Amounts use decimal strings; separate currencies have
separate totals. Historical uncovered attempts remain visible as incomplete
coverage. Account aggregation currently reads the owner's recorded history;
large histories may eventually need a dedicated aggregate/read model.

## Git source and Linux verification

Application commits:

- `cee32b6ffb4aa2b6d0edbe57c21b94324720aff1`: accounting/API/UI implementation.
- `6003bee325c8c13cb4fc4fdaddcf12f23745ca03`: retain cancellation assertions
  while checking preserved accounting.
- `5d4cfc428dc829c9bfc066801630df05dd2b05fe`: include usage and acceptance
  evidence in both site packaging paths, with import-closure/hash checks.

Source arrived on the Linux manager through Git fetch and exact checkout.
All 28 regression stages passed: 1,072 tests, zero failures, errors or skips.
Receipt:
`/root/ppt-prepare-task-5d4cfc428dc8-c4c88c68d2-checks/summary.private.json`.
No local tests, compilation, builds, previews or PowerPoint runs were used.

Two images were built from that exact source with `latest` and verified
revision/context labels:

| Role | Immutable digest |
| --- | --- |
| Site | `sha256:76d501e802eceada8a8d950fbf28034a5721d090d01c856f79a124b6e06a35a7` |
| Controller | `sha256:a7d11ee28c57628c86fefbb285fc08537962950f07c04dccd1c268245a8f6808` |

Build receipt:
`/root/ppt-review-usage-5d4cfc428dc8-build-20261102T043144Z-04d16235/receipt.json`.
The existing independent validator and shared Agent runtime/catalog are retained.

## Canary authority and environment

The source is project `c9d480b5-be18-46ed-839b-e65a9bd57eda`, review Version
`b6b02806-506a-414e-a314-8d3ec230f9f2`, source task
`f5d0d14e-df23-48ea-b116-4737281abd75`. Its delivered PPTX remains
`19d9e26451ebb621f4cfa9452edaa7b5381c2a075d0c26bbf587933937e3207d`.

The operator captures the source using a real PostgreSQL REPEATABLE READ READ
ONLY transaction and source/hash/owner/confirmation checks. New business writes
are confined to a fresh private PostgreSQL database and copied data directory.
The in-process clone uses the legitimate PPT service caller and actual business
routes, connector and independently scoped validator. No public auth route or
production business row is changed.

Pre-submission failures exposed operator compatibility requirements, all before
model admission: Python needs `Path.is_junction` (the verified extracted runtime
is the deployed Python 3.13.5); Kubernetes inventory must use real API List RVs;
this Docker host requires an ordinary dedicated bridge for loopback port
publication; current Pi v2 uses digest `8fce72ba69b4d0acc5fa05fbe9c48c2fe850a33adc6acc44fb1123e8235d2028`.
The build receipt for that already deployed worker is
`/root/ppt-model-worker-4162b27ddb97-build-20261031T141151Z-d40bf3b8/receipt.json`.

The first actual clone HTTP admission returned `review_checkpoint_unavailable`
before submitting a model task. Frozen confirmations used `+08:00`; a default
UTC clone changed their `isoformat()` representation. The corrected capture
records the source database's actual `SHOW TIME ZONE`; every cloned connection
uses that timezone. Original confirmations and hashes are never rewritten.
Earlier owned database/network/validator resources and temporary credentials
were removed with exact ownership checks; private failure evidence and dumps
remain available.

## Actual revised delivery

The corrected canary completed exactly one real `ppt.revise.v1` v2 submission:

- Business task: `a902db70-64c8-4d77-b4ba-0b442bfef8dd`.
- Remote task: `4235a001-ad7b-46bb-8d48-e050bbeeb721`.
- New review Version: `61cdd4fb-15ff-4d58-bea1-b70da98ff2fd`.
- Downloaded PPTX: 53,936 bytes,
  `830f1de2a7c17e3b8ca143e0f91fe500e94e853dcf0abcff4ac349605e3bb9f1`.
- Page 4 changed; the other nine source SVGs remained byte-identical. The ten
  normal previews and ten independent final-PPTX previews passed their route
  and hash checks. All seven independent programmatic gates passed, with new
  receipt `6c7f272424e5100e2c649d4076333e0094d183e10d0d1e3172e4d9444c5fe510`.
- The original copied files and lineage/human records remained unchanged. The
  new review has zero human records and no publication authorization.
- One candidate-received ACK, observed physical stop, one durable usage receipt,
  and two unchanged read-only observations of the remote/ledger/task/account
  projections were verified. The repeated observations did not submit work or
  add another usage row.

Evidence root: `/root/ppt-review-revision-20261102-r6`. Its
`business/revision-receipt.private.json`,
`business/revise/usage-verification.private.json`, verified database dump and
`launch-completed.json` retain the result. Owned namespace/PV/PVC, PostgreSQL
container/network and temporary credentials were removed after exact identity
and stop checks. The retained database dump SHA256 is
`74c1341b8228c5e5bd17b1626eb7da3ca825dd9b9c9fb3b4363d1b9be561901c`.
There were zero production business database writes. This is a successful
**clone canary**, not a new Version inserted into the user's production project.
The revised download was also retained in the local workspace without running
any local renderer, test or Office application.

The actual Pi terminal response supplied no token counters, model or supplier
cost. Its frozen normalized receipt therefore contains unknown values. Static
trace confirms the cause: `agent_service/engines.py` constructs Pi's completed
`NativeOutcome` without usage, while `runtime/adapter.py` leaves `token_usage`
unset. Worker/store/API preserve that missing value; the PPT connector does not
drop a supplied counter. This release proves receipt attribution and truthful
unknown handling, **not completion of upstream Pi token collection**. Adding
that upstream collection remains follow-up work, without rewriting this frozen
receipt or inventing historical costs.

## Guarded rollout and production observation

The site/controller rollout completed at `2026-11-02T06:26:40Z` using the exact
two digests above. Deployment lock and read-only admission guard were released.
No migration, replica scaling, Agent/catalog change or validator rebuild took
place. Acceptance remains enabled with the original validator digest
`ac362a76a9756939421134248f06d5ec0734963005af15aeebc0513c0cfd3207`.
Release evidence:
`/root/ppt-review-usage-site-controller-5d4cfc428dc8-20261102T062454Z-2fc1af13`.

The read-only production audit verified installed usage/API sources and actual
task/account/quota projections. Historical proxy receipts provide partial
observed account totals; this old Pi task remains unknown and does not display
the account total as its own usage. Original source revision 26, review revision
2, PPTX SHA and all seven programmatic gates remained unchanged. The visual
record still passes; the desktop record remains pending and publication stays
draft/unauthorized. Audit evidence:
`/root/ppt-acceptance-audit-20261102T064702Z-8840bdd2-0e4b-406b-b688-77be20bc6a52`.
The audit issued zero SQL writes, row locks, model submissions or publications.

The authenticated production browser loaded the new assets after reload and
showed separate “本人累计用量” and “本任务用量” regions, partial/unknown
explanations, unknown supplier cost and disabled quotas. No browser task or
human-review record was submitted during this verification.

## Remaining human boundary

Actual Microsoft PowerPoint opening, content editing, saving and reopening
remain pending. Existing visual and desktop records retain their original
meaning. New revision results remain review drafts, with no inherited human
pass or automatic publication. This work does not apply/archive the wider
OpenSpec change.
