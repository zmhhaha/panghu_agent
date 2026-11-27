# Recover a completed scene source with authoritative author usage

When a silent SceneSpace run fails with `scene_appearance_usage_unknown` after
its author completed and its source/plan were frozen, an explicit recovery can
continue from previews without generating another world. The original failed run,
stopped scene journal, task, unknown author event and artifact remain unchanged.

`POST /api/production-runs/{source_run_id}/scene-source-recovery` uses the normal
authenticated owner, CSRF protection and required `Idempotency-Key`. It returns
202 with a new linked production run. The request has exactly these fields:

```json
{
  "expected_revision": 4,
  "expected_source_run_sha256": "<canonical source production-run digest>",
  "expected_scene_sha256": "<canonical stopped source scene-journal digest>",
  "expected_usage_sha256": "<source task usage.sha256>",
  "expected_author_job_id": "<original completed author job ID>",
  "expected_author_input_sha256": "<original author input digest>",
  "expected_author_result_sha256": "<original author result digest>",
  "expected_source_sha256": "<original source identity>",
  "expected_plan_sha256": "<original frozen plan identity>",
  "expected_configuration_sha256": "<original configuration identity>",
  "expected_usage_evidence_sha256": "<fresh Agent usage_evidence.sha256>",
  "execution_deadline_at": 1795755216.7558527
}
```

The deadline is a caller-selected absolute ceiling. It must be in the future and
no later than the original scene's legitimate deadline. The endpoint never
changes a service deadline or creates a new full work window. Film, adopted plan,
configuration, original author document/experiment ID, input, result, source,
camera plan and author job are copied exactly. The body cannot supply replacement
content, source, usage counters or a plan.

The server performs an actual authenticated GET of the original author job and
reads its stopped artifact using the existing gateway. The job must be completed,
stop-proven and bound to the expected author profile/revision, attempt and fence.
Its control-owned `agent-model-usage-evidence.v1` must be authoritative, complete,
cover all attempts, match the same job/attempt/fence/execution binding, and have
the expected canonical digest. Input plus output tokens equal total tokens;
cached input is already included and is not charged twice. Worker output or
caller-supplied/self-hashed usage cannot replace this actual control GET.

Recovery is available only before any downstream preview, clip, review, repair
or export submission. A null child ID with `attempted=true` is an uncertain
original submission and is rejected; it cannot authorize a new key. A stopped
scene may be `cancelled` after cleanup while its original production run retains
the failed usage error. Only that original failure is accepted. A repaired or
previously recovered source cannot be recursively recovered.

The main DB transaction rechecks owner, project revision, adopted inputs, source
run/journal/usage hashes, idle project and one-recovery limit after the external
GET/artifact reads. It commits one new production-run intent before any separate
scene/task DB write. The normal workflow idempotently installs its new task and
scene, then enters `preview`; it never enters authoring or submits the old author
again. A crash after task insertion reuses the same task/run/scene IDs and cost
event. Same-key/same-body replay returns that run. Changed same-key input and a
second key for the same source are rejected.

The new task copies the original frozen plan, usage limits, baseline, history,
events and settled reservations. Only in this new lineage is the original
completed generation event's unknown measurement supplemented with authoritative
author spending. Its call count cannot decrease. Every other expense remains
charged; an unknown remainder or an exceeded budget blocks recovery. The source
unknown event and full source-task digest remain in the linked recovery evidence.
Review and the existing one-repair allowance use the same remaining budget and
normal gates. The recovery itself adds zero author/planner/resource/model jobs.

This endpoint provides technical recovery, not visual or artistic approval.
Actual static/clip review, export checks and user acceptance retain their normal
boundaries.

Validation: the new recovery suite has15 passing cases plus one additional
cross-DB crash test. The related regression command ran51 cases (50 existing
SceneSpace production/recovery/store cases plus the already-covered new HTTP
case):48 passed initially; three pre-existing fixture failures were
reproduced using the exact `0e6b43e` versions of all four runtime modules in an
isolated Python process. The HTTP fixture lacked the current auth issuer/HTTPS
context. Two repair fixtures expected the old same-plan error while the admitted
structural policy correctly rejected same-revision edits as
`scene_space_plan_revision_invalid`. Their narrow fixture updates retain all
failure, original-source/sample-preservation, paid-usage and no-extra-job
assertions; only those three cases and the added crash case were rerun (4 passed).
This covers66 distinct passing cases without claiming a repeated full suite.
