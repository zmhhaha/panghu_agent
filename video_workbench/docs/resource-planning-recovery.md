# Recover only a failed resource-planning stage

`POST /api/projects/{project_id}/joint-plan-operations/{operation_id}/resource-recovery`
creates one explicit resource-only recovery after a stopped resource child
failed. It uses the normal authenticated write/CSRF contract and requires an
`Idempotency-Key`. The request body contains exactly:

```json
{
  "expected_revision": 3,
  "source_version_id": "original-candidate-uuid",
  "expected_head_version_id": null,
  "expected_joint_input_sha256": "64 lowercase hex characters",
  "expected_joint_result_sha256": "64 lowercase hex characters",
  "expected_resource_input_sha256": "64 lowercase hex characters",
  "expected_producer_catalog_sha256": "64 lowercase hex characters",
  "timeout_seconds": 120,
  "execution_deadline_at": 1800000000.0
}
```

The API reads the original resource Agent job through the configured private
gateway. It verifies its terminal state, actual attempt/fence, profile and stop
proof. It checks owner, project revision, current candidate/head, unchanged
content binding and all frozen hashes before that read and again when committing
the recovery. It does not accept a caller-supplied stop proof or resource result.

The original failed operation, failed candidate and failed Agent job remain
unchanged. A new linked operation and candidate copy the validated original
planner result, knowledge binding, completed planner job ID, frozen resource
document, producer catalogue and input reference. Only the resource-child
operation/key is new. The existing workflow advances that child directly and
does not upload or submit another main planner task.

The202response has the ordinary joint-operation view: `id`, `kind` set to
`joint-plan-resource-recovery`, `status`, `error`, original main `job_id`, new
`version_id`, `created_at`, and any library binding. The normal project joint-plan
GET exposes the new pending candidate. Adoption remains blocked until the real
new resource child is completed, stopped and its result/digests are validated by
the normal coordinator. An empty resource plan must be the actual validated Agent
result; recovery never supplies a fallback empty plan.

Only one recovery is admitted per original operation. Identical key/body replays
the same operation before checking later project state or deadline. A different
key cannot create a second recovery. A recovery cannot itself be recovered.
Unknown child submissions reconcile with their original child key and do not
resubmit. Read reconciliation by the operation's stored owner/idempotency key
must precede any decision about an uncertain HTTP response.

The new operation records `resource_recovery` lineage, including source operation,
candidate and job IDs, source document hashes, actual observed stop proof and its
digest, old deadline, and an explicit one-extra-resource-job budget. Its child
deadline is `min(now + timeout_seconds, execution_deadline_at)`, with an explicit
timeout of1–600seconds. The old deadline is never rewritten. Callers must cap this
new explicit deadline by the remaining overall production/operator window.

This consumes one additional Agent job and actual provider usage. A production
path that previously had a worst case of6jobs now has a worst case of7when this
recovery plus the optional author-repair/review pair is used. Consumers must
record the extra attempt and keep spent usage; recovery does not reset time,
token, render or paid-attempt budgets. It provides no artistic acceptance,
publication, media regeneration or automatic adoption.
