# Preparation request capacity repair — 2026-10-30

The reported preparation jobs `d80d4275-b224-4f3f-979d-4a2255657bea`
and `a836d0fd-280a-41f4-ac01-f0de6f8e8312` failed before Agent submission.
Read-only reproduction against the controller found that `build_agent_task`
successfully resolved both database confirmations and the checkpoint, but
`JobRequest` rejected the resulting 45,650-byte canonical UTF-8 task at its
32,768-byte limit. Neither job had an Agent outbox, remote job or generated version.
The connector incorrectly reported this request validation failure as
`EXECUTION_INTERRUPTED`.

## Repair

Exactly the five audited PPT profiles (`plan_initial`, `plan_options`, `prepare`,
`generate`, `revise`, each named `ppt.<phase>.v1`) receive a server-owned 256 KiB
task allowance. Submission and worker handoff use the same policy. Unknown and
non-PPT profiles retain 32 KiB. No model fields, task-contract serialization,
confirmation content, uploaded data, deadlines or ownership rules change.

Pre-submission task size rejection and other request construction failures have
distinct safe errors. Full exception text, uploaded material and confirmation
content must not appear in public events or logs. Such rejection does not claim
that a worker started or stopped.

## Validation and release scope

Run focused tests only on the Linux server, using exact Git-fetched source,
isolated fixtures and no production database or model credentials. Cover both
wire boundaries, UTF-8 byte counts, unchanged default/nesting limits, rich
confirmed-plan preparation, and failure before outbox/client submission.

Required runtime changes include the PPT controller and Agent control/Pi images;
public error copy also requires the PPT site image. Publish `latest`, deploy
immutable digests, and record source/context provenance. A new worker digest
requires fresh environment/binding/runtime evidence. Preserve current research
and video configuration and all artifact/review/publication gates. Do not replay
the broad deployment/migration script.

Verify the two reported stored tasks read-only using deployed request/handoff
validators. Do not rewrite failed records, reset their deadlines, or automatically
retry a business generation. A passing request regression does not establish that
a PPTX or PowerPoint acceptance has succeeded.

## Executed server checks and rollout

Runtime source is application `7a506d4b96c977cf5213e847b0aa32ffc12f930d`,
merged into application main and recorded by outer main `f686b1a` before release.
The manager fetched this source through Git; no application source archive was
uploaded. All 165 focused server checks passed, with zero skips or failures:

`/root/ppt-prepare-task-7a506d4b96c9-92c12047ec-checks/summary.private.json`

An additional 14 checkpoint compatibility cases passed from test-only commit
`e1aa6d8ea43de8272137d4256de81601c04da674`, with the runtime source tree proven
unchanged. Evidence:
`/root/ppt-checkpoint-e1aa6d8ea43d-3379527e46-checks/summary.json`.
These checks used isolated cached Linux images without production data or model
credentials. No local tests or builds ran.

Four `latest` images were built and pushed on the ARM64 manager. Each has the
exact runtime Git revision and context hash labels; pushed manifest/config
digests were verified through Docker's configured registry transport:

| Role | Registry repository | Deployed digest |
| --- | --- | --- |
| Site | `arm-cluster-master:5000/ppt-service-site:latest` | `sha256:2b3209f7a6b23efc3fa0f4bf65b432feb190d4bc11e95a40fc23df8be768664b` |
| Controller | `arm-cluster-master:5000/ppt-agent-controller:latest` | `sha256:01b54b7b370b91b5b2b05921cee34a4c8f2d3936dadffb3beb8f4bd770b5ac50` |
| Agent control | `arm-cluster-master:5000/agent-service-video-control:latest` | `sha256:b3356d27dbdc31c02030f7896314efac3d4d4eceff51eb0c575c219d2647db25` |
| Pi worker | `arm-cluster-master:5000/agent-service-pi:latest` | `sha256:e99d3a2f343e35be726635c303c0dfc6ac844a25c29b2fd7f93b2423254e9717` |

Build receipt:
`/root/ppt-prepare-capacity-7a506d4b96c9-build-20261030T152502Z-844b59c1/receipt.json`.
The first build operator attempt stopped before building because direct HTTP
registry inspection returned 400; the successful attempt used Docker's native
registry transport and independently hashed its raw manifest bytes.

Fresh runtime prerequisites passed for all five PPT profiles. Each used one
real fixed-echo upstream request, with isolation, lease expiry and complete
process/resource cleanup evidence. They did not execute user PPT tasks or prove
domain generation quality. Evidence:
`/root/agent-service-deploy-20261026/ppt-prepare-runtime-7a506d4-r1/runtime-preflight-set.json`.

The release retained profile revision `v2` and the exact PPT bundle. Advancing
the profile revision would make the existing `v2` planning checkpoint incompatible.
Only the five existing PPT route binding revisions changed; historical registry
rows, hosts and pools and unrelated research/video routes remained intact. The
new UID reservation starts at 46265536, with 65536 IDs. Current shared control
source `f90ba1c` (including both recent video fixes) was retained in the new source.
Agent publication records:
`/root/agent-service-deploy-20261026/ppt-prepare-publication-7a506d4-r1`.

Site/controller rollout completed at `2026-10-30T15:37:08Z`. Both new pods ran the
exact digests, and the controller exposed 256 KiB for known PPT tasks and 32 KiB
for default/unknown profiles. Authentication configuration, authenticated feedback
access, database, PVC, existing validator, RBAC, resources and other configuration
were preserved. No migrator or production business row edit ran. Deployment lock
was released and the Agent dispatcher was restored to one ready replica.
Site/controller receipt:
`/root/ppt-prepare-site-controller-7a506d4b96c9-20261030T153532Z-a6b4a776/release-receipt.json`.

Finally, both exact reported 45,650-byte stored requests passed the deployed
controller builder, Agent API request/envelope validation, old-checkpoint
compatibility and the new Pi image's envelope/profile validators. Both still
select direction A at confirmed plan revision 2. Their original deadlines and
failed records remained unchanged; no outbox, replacement business task or model
request was created by this replay. All five PPT routes were ready. Public ping
returned 200, root redirected to authentication, and anonymous API access returned
401. Evidence:
`/root/ppt-prepare-live-replay-20261030T154233Z/summary.json`.

The user may refresh the project and click **生成演示稿** using the existing
confirmed plan. This release resolves the demonstrated pre-submission failure;
it has not generated or accepted a PPTX/preview from a new business execution.
Earlier provider failures and full generation/revision/PowerPoint acceptance
remain separate pending work.
