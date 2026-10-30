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
a PPTX or PowerPoint acceptance has succeeded. Actual server checks and rollout
results will be recorded separately after execution.
