# PPT Agent receiver validation and generation blocker, 2026-10-29

Production PPT has **not** switched to Agent Service. Schema4 migration, PPT v2
route publication, real Jobs HTTP connector/ACK, generation/revision acceptance
and review/download verification remain pending. PA3/PA4/PA5 remain incomplete.
The existing native PPT backend and its users' projects remain in service.

Application execution source f92e23b279a4e2561d8bb9cde62642c28de8761d is retained.
Receiver fixes 1852eaaf8d7158dc085d03e5c42d54a2372c6f05 and
4cbdb8023f15209560659ce964359a45cf64fb2d were committed/pushed/fetched through Git;
outer ee1a4ef34e9ed42f51d7053138ae841131037c4a pins the final receiver source.
The final source passed 982 focused isolated server tests with zero skips,
failures or errors, plus tsc/Vite production compilation. No local tests/builds
were run. These overlapping suites are not added to the earlier 978 count.

- Final checks: /root/ppt-agent-4cbdb8023f15-cf3c468b55-checks/receipt.json.
- Final receiver build: /root/ppt-agent-4cbdb8023f15-build-20261029T141106Z/receipt.json.
- Execution build: /root/ppt-agent-f92e23b279a4-build-20261029T131351Z/receipt.json.
- Integration state: /root/agent-service-deploy-20261026/ppt-agent-integration-progress-4cbdb802-20261029.json.

Pi is zmhhaha/pi-cage e0262e65e43c6924164e995394f715dc5d19c210, Pi0.99.2.
The two actual builds measured identical Agent control and Pi image digests;
native execution evidence keeps its original source and immutable image identity.
Receiver site/controller/validator provenance remains a separate complete build
receipt with verified Git ancestry. No single-commit end-to-end run is claimed.
Final independent validator image:
arm-cluster-master:5000/ppt-agent-validator@sha256:304ec54fb1800eeeafa81aa29e2d7afc383c073270b624eb2a5e005720ec3006.

The connector now durably flushes artifact downloads in a private fenced sibling
scratch before atomically installing them. Recovery resumes the same remote job,
retains its deadline and verifies every frozen size/hash. The independent prepare
gate recreates only the known empty svg_output in disposable validation scratch
and preserves the original project basename. Real OfficialGates still execute;
the original file-only checkpoint, approved specs and failed evidence are intact.

Synthetic business root:
/root/agent-service-deploy-20261026/ppt-business-agent-f92e23b-r1.
No production business database or existing user project was used by the canary.
The following phases completed native execution, actual business persistence and
independent validation; both confirmations used the real synthetic HTTP handlers:

- plan_initial: candidate-ppt-plan-f92e23-r1;
  business job 4cf7dc9c-9069-4c7f-a973-74b3997153ee.
- plan_options: ppt-plan-options-41eda04401b3;
  business job 5b93bc00-e596-42f0-956b-c5b3f18718ef.
- prepare: ppt-prepare-41eda04401b3;
  business job 10464c8d-b42d-43c1-9525-92e479eaf621.

The final validator also revalidated the original initial/options snapshots under
fresh private authorities without model requests or opening the SQLite business
database. Supplemental receipts are BUSINESS/planning-rechecks-final4cb-r1/
plan_initial-final-validation.json and plan_options-final-validation.json.
Prepare used the real acceptance/persistence path; its sidecar is
BUSINESS/delivery-sequence-73c754b3b640/prepare-final-validation.json.
Every supplement binds the original receipt and parent hashes, complete immutable
snapshot roster, fresh request nonce, actual validator image/Pod UID and stop proof.
Original authorities were not overwritten to permit a changed validator image.

Generation business job df234cc7-0a53-4059-8a0d-0243d704f7dd did not produce an
accepted candidate. Native candidate ppt-generate-73c754b3b640 failed after eight
model calls. One explicitly reviewed recovery candidate ppt-generate-0648f6e5a024
failed after twelve calls with the same top-level SSE error: upstream_error /
Responses stream reported a failure (HTTP200, 151 response bytes). The recovery
native deadline 1793285408.2171113 was earlier than the original
1793285518.2250133; no existing deadline was extended. Both actual worker Pods
terminated, were deleted, and their model ledgers/native results remain private.
There was no third paid attempt and no revision submitted.

The exact generic error text is produced by llm-service's Responses failure
normalization. Its current records do not retain the original provider error code;
the underlying reason is unresolved, not established as timeout, image failure or
context exhaustion. The second run's last successful input count was 59563 tokens;
its preceding read was a Markdown reference, not a PNG/image call. Failed native
execution was not imported or represented as successful PPT review/publication.
The isolated SQLite generation job remains unclaimed and queued; this private
store is not served or processed by a production controller.

All shared-dispatcher maintenance windows were restored with zero active/queued
Agent jobs and no occupied slot. Owned validator namespace
agent-candidate-ppt-f92e23b and Retain local PV candidate-ppt-f92e23b-data were
deleted only after exact ownership/path/claim/UID and no-Pod checks. Business and
all evidence files remain on the server. No production schema/role/Casdoor/user
row changes, UID reuse, weakened gates, automatic model loop or publication occurred.
Fresh validation infrastructure is required before any later continuation.
