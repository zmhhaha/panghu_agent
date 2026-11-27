# Historical model usage on authorized job reads

`GET /v1/jobs/{id}` and `GET /v1/jobs/by-idempotency/{key}` freshly read the
control-owned model response ledger after the existing tenant, caller and profile
authorization checks. When ledger rows exist they add `usage_evidence`, and the
existing bounded `usage` field reflects that evidence. Submission/replay responses
and stored worker/job outcomes retain their original semantics and bytes.

The evidence schema is `agent-model-usage-evidence.v1`. It binds `job_id`, current
`attempt_id`, `fence`, `state` and `execution_binding_sha256`. `all_attempts=true`
means every attempt of that job was included, including failed earlier attempts;
`attempt_count` and per-attempt stopped custody accompany every distinct admitted
request's attempt/fence, execution-request hash, model-request body hash and
immutable supplier-response hash. The public proof contains bounded counters and
hashes, never prompts, tool data, raw responses, grants or credentials. Its
`sha256` is the canonical JSON digest of all fields except `sha256` itself.

`completeness=complete` requires all requests to have completed HTTP 200 supplier
Responses bodies with one consistent terminal usage record, and every attempt to
have control-validated stopped custody bound to its retained worker allocation
and signed result receipt. The current job and current attempt must agree.
Historical grant or lease expiry does not invalidate already retained evidence;
the reader never requests a new grant or calls the model. Supplier JSON and SSE
are supported; repeated identical terminal events count once per request.
Conflicting, malformed, missing, uncertain, active or unbound evidence remains
partial. No ledger rows produces no new evidence, rather than invented zero cost.
More than 1024 requests or attempts produces bounded partial evidence.

The aggregate `request_count`, `input_tokens`, `output_tokens`, `total_tokens`,
`cache_read_tokens` and `cache_write_tokens` use supplier counters. Missing counters
stay null; sums are checked for bounded integer overflow. Cached tokens are
already included in input tokens and are never added again to total tokens.
Known token counts do not establish monetary cost: `reported_cost=null` and
`cost_status=unknown` remain explicit. Partial evidence may retain known subtotals
but cannot authorize a complete budget settlement.

`authoritative=true` identifies control-measured, stopped and bounded custody;
consumers must additionally require `completeness=complete`, validate the evidence
digest, and match its job/attempt/fence/binding to the same freshly read stopped
job. A prior response is not a promise that a future read is byte-identical:
historical ledger completion may change the evidence digest. The original worker
outcome is never rewritten to simulate a newly completed task.
