# Continue one cancelled review without reauthoring

This is a separate, explicitly acknowledged exception for one stopped
source-recovery run. It does not loosen the normal `usage_unknown` gate or
reuse the consumed `scene-source-recovery` endpoint. Existing jobs, scene/task
rows, deadlines, failed outcomes and usage events remain unchanged.

The owner calls
`POST /api/production-runs/{parent_run_id}/scene-review-continuation` with the
normal authentication, CSRF token and required `Idempotency-Key`. The body has
exactly these fields; hashes refer to the actual stopped parent and original
completed author, not caller-generated replacement media or costs:

```json
{
  "expected_revision": 4,
  "expected_source_run_sha256": "<parent production-run digest>",
  "expected_scene_sha256": "<parent stopped scene-journal digest>",
  "expected_task_sha256": "<parent raw task digest without derived status>",
  "expected_usage_sha256": "<parent usage.sha256>",
  "expected_author_job_id": "<original completed author>",
  "expected_author_input_sha256": "<original input digest>",
  "expected_author_result_sha256": "<original result digest>",
  "expected_source_sha256": "<unchanged source digest>",
  "expected_plan_sha256": "<unchanged plan digest>",
  "expected_configuration_sha256": "<unchanged frozen configuration digest>",
  "expected_usage_evidence_sha256": "<actual author control usage evidence>",
  "expected_review_job_id": "<old cancelled reviewer>",
  "expected_review_input_sha256": "<old reviewer input digest>",
  "expected_review_usage_evidence_sha256": "<actual partial old control evidence>",
  "expected_lineage_job_ids": ["<sorted unique existing IDs>"],
  "execution_deadline_at": 1800000000,
  "accept_legacy_usage_unknown": true,
  "accept_original_token_cap_unproven": true,
  "allow_export_with_legacy_unknown": true
}
```

The three acknowledgements are mandatory true booleans. They acknowledge that
the original total token cap cannot be proven and cumulative spending remains
unknown. A future deadline alone is insufficient. The server fixes all new
limits; the caller cannot raise them through this request.

The parent must be a cancelled, physically stopped, first source-recovery run,
with a completed original author, no repair/plan revision/export, completed
static and clip previews, and exactly one unknown usage event: that parent's
cancelled reviewer. Unknown baseline tokens, other unknown model/render events,
active reservations, uncertain preview submissions and recursive continuations
are rejected. One continuation is allowed for the original production lineage,
including after the new grant fails or is cancelled. Same-key identical replay
returns the same run even after its deadline; a changed body or another key does
not create more work.

The service performs actual author/reviewer GETs and verifies stopped attempt,
fence, input/result/usage hashes and the original author's collected artifact.
It reads actual retained preview results and media and verifies source, plan,
content hashes and local sampled-file bytes. Old successful previews are reused;
neither the author nor their source is regenerated. No cancelled reviewer result
is accepted as a completed review. Existing planner/resource IDs are derived
from the actual adopted version, operation and resource-recovery lineage, and
compared with the expected list. The full lineage must fit the unchanged seven
distinct Agent-job limit after adding one reviewer.

The main DB commits one new run/grant intent after a second CAS, before the
separate task/scene stores are installed. A restart after any insertion installs
the same IDs and grant ledger. An uncertain job or renderer submission is looked
up by its original key; the workflow does not change keys to spend again.

The fixed new grant permits no author, one reviewer, at most eight provider
requests across all attempts of that job, at most 8192 output tokens per
forwarded provider request, a future absolute deadline no more than one hour
after grant issuance, at most 600 measured CPU render seconds, one export and
zero repair jobs. Failed/cancelled work remains charged. The 262144 token
reservation is an admission estimate and known-usage stop threshold, **not an
input-plus-output token hard cap**. The unchanged original plan and its limits
remain available as historical data rather than being rewritten as a new hour.

Per-job provider limits must be implemented by the control/proxy release.
Readiness requires the exact `model_request_limits` capability protocol from
the authenticated `/v1/capabilities` response. The new POST carries top-level
`model_limits={max_requests:8,max_output_tokens:8192}`; subsequent actual job GETs
must echo them. Acceptance additionally requires complete authoritative
`agent-model-usage-evidence.v1`, all attempts/stop custody,
`model_limits_verified=true` and those exact limits. A worker promise or a
self-hashed caller record cannot satisfy these conditions. Deployment still
requires independent verification that both the API and proxy run that release.

The original ledger is copied unchanged into the new task's `usage`; all old
reservations remain under `legacy_reservations`. New reservations and events go
only into `continuation_usage`, whose lineage and explicit grant limits are
separate. Public `usage_status` and scene accounting always report cumulative
`usage_unknown`, `original_total_token_cap_proven=false` and no general export
authority. `continuation_usage_status` reports only the new allowance. Missing
or unverified new usage is recorded as unknown and blocks both further model
work and export. The exception is never expanded to new unknown events.

For a quality-guided render, the renderer's usage identity binds the new grant
ledger explicitly; the document also carries the grant/legacy usage identity,
`budget_scope=explicit_new_grant_only` and cumulative unknown status. This does
not claim the original cumulative ledger became known. A new reviewer must
complete independently, pass the ordinary image gate and have verified new
usage before the single export can begin. A rejection stops without repair.
Render usage is settled before the result is adopted; overrun or unknown render
time blocks completion. The output remains a technical draft, without automatic
artistic approval, new-content validation or WikiSkill publication.

The integration tests use synthetic media to verify custody, budgets,
permission boundaries and recovery. They do not claim image quality, continuous
viewing or a deployed grant. Actual release, jobs, media decode and cumulative
history evidence must be collected separately.
