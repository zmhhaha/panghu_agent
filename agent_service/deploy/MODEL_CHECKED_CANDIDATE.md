# Model checked v2 candidate entrypoints

New `video.picture-learning-review.v2` candidate operations select the explicit
`video.picture-review-v2-model-route.v1` launch contract. It fixes public wire
alias `llm-trusted`, effective target `gpt-6.2-sol`, upstream `custom`, trusted
tier and Responses wire protocol. The operator must independently pin the
expected route revision; it is never inferred as acceptable from a changed
service response. There is no alias fallback or model request in these checks.

`agent_service.deploy.model_checked_candidate.prepare_checked_candidate`
performs a fresh trusted read **before** invoking the operator's existing
`reserve_uid` authority. It then calls the real `acceptance_probe.render_probe`
with the new contract, which checks again before rendering. The returned
`before_uid_preflight` and frozen manifest annotations preserve alias, target,
revision, snapshot digest, observation times and phase.

Immediately before its existing sealed once-only create operation, the operator
uses `create_checked_candidate(config, manifest, route_revision=...,
create_once=existing_once_create, reader=trusted_readiness_reader)`. This checks
the frozen proxy CLI flags and projected host configuration, fetches fresh route
metadata, and only then calls `create_once`. This wrapper does not supply a new
Kubernetes client, UID allocator, grant, retry policy or authorization. Those
remain with the existing reviewed operator.

The default reader GETs only
`http://llm-service.llm.svc.cluster.local/health/ready`, with bounded response
bytes and time, no redirects, no environment proxy, credentials or retries.
Operators outside cluster DNS may inject their existing trusted, read-only
health transport. Its observation must identify the same fixed endpoint and
carry start/end times from the current call. A saved readiness snapshot cannot
substitute for a new read. Transport callbacks are administrator code, never
model output or domain task fields.

The resulting proxy Pod keeps the existing harness command and receives sealed
`--model-route-contract video.picture-review-v2-model-route.v1
--model-route-revision <pinned-revision>` arguments. In the **new CLI**, both
`acceptance_probe render` and `acceptance_probe proxy` require these fields for
v2 and reject omission. `ProbeAuthority` repeats the fresh check before private
job/grant creation and records `model-route-preflight.json`, which must be kept
even if the first provider request fails. Successful probe evidence also embeds
the check. This runtime check occurs after the Pod exists; it does not replace
the operator's earlier pre-UID and pre-create checks.

Old in-process APIs without this opt-in contract retain their legacy behavior
for frozen tests and previously constructed contracts; old frozen code, images,
manifests and evidence are not rewritten. New v2 operations must use the checked
entrypoints and new CLI. A new ordinary worker image must contain these modules;
mounting the new harness over an older frozen image is not a valid release.

Validation uses the actual manifest entrypoints and a real isolated SQLite
authority with mocked readiness only. Wrong alias/target/revision and unready,
stale or foreign observations stop before the side-effect callbacks; a route
change before create prevents Pod creation, and a runtime mismatch creates no
private state, job or grant. Legacy regression checks remain separate. None of
these tests establish a live candidate admission or permit a model retry.
