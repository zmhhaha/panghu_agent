# Job-scoped model request limits

New callers may submit an optional top-level `model_limits` in `JobRequest`:

```json
{"max_requests": 8, "max_output_tokens": 8192}
```

Both values are strict positive integers. These limits only restrict an existing
admitted Kubernetes execution using the scoped model proxy; other adapters reject
the option. The authenticated capabilities response advertises
`model_request_limits` for the supported execution route. Actual API and proxy
release identities must also be verified during deployment.

The immutable stored job request supplies the limits. Workers cannot set, replace
or raise them. Admission locks the job and counts all of its attempts, including
uncertain requests. Proxy/process restart does not replenish the allowance.
Completed identical requests replay without another admission or provider call.
Existing global and per-profile limits still apply and may be stricter.
Limited jobs use serial model turns. An uncertain prior admission, or a retained
response without complete standard supplier usage, blocks every new request body
across all attempts. It is never assigned zero spending to let a worker continue.

Before forwarding, the proxy lowers a valid requested output maximum to the
smaller of the original maximum and the job maximum. This supports existing
workers that request their usual 32768 output tokens. Invalid original requests
remain rejected. The normalized request is the durable admission identity.

Each limited admission atomically records its exact normalized output maximum
and job request maximum in `agent_model_request_limits`, without prompt/image
bytes. Stopped authoritative usage evidence includes these bounds and
`model_limits_verified`; missing records or known supplier output overruns do not
verify the limit. This is separate from complete usage and native stop evidence.

These are request-count and per-request-output limits. They do not provide a
hard input-plus-output token budget, monetary cap, another time window, new
runtime admission, or permission to treat missing usage as zero. Any explicitly
authorized legacy accounting exception belongs to its own application contract.

Old requests without the field retain their original serialized wire shape,
historical usage proof shape and behavior. The new accounting table is additive;
no previous request, response or outcome is rewritten.
