# Bounded upstream rejection evidence

The model proxy keeps a durable admission before contacting the fixed upstream.
A non-200 response does not complete that admission or establish known usage.
The existing `agent_model_requests` row now retains its actual HTTP status and a
bounded valid MIME. It retains the decoded HTTP response body only after a complete
read within both the grant and `min(64 KiB, max_response_bytes)`.

If the body exceeds that bound, the stream fails, or authorization expires, the
row retains the known status/MIME with `response=NULL`. It never stores a prefix
as a complete body. Usage evidence reports no body hash for NULL; a genuinely
complete empty response may have the SHA of empty bytes. No new table or DDL is
required and old stored rows are not rewritten.

The state stays `admitted`. Worker-facing errors remain the generic uncertainty
error; the raw error body and headers are not logged or returned to the capsule.
Identical replay still refuses to issue another request, and limited jobs stop
new bodies after uncertain usage. A response which looks like successful usage
but has HTTP 400/429/503 is still not successful usage. The ordinary HTTP 200
JSON/SSE path, per-job limits, durable counting and external-stop requirements
remain independent.

For new candidate route checks, use the actual entry points described in
[MODEL_CHECKED_CANDIDATE.md](MODEL_CHECKED_CANDIDATE.md). A route metadata check
does not prove model quality or physical execution admission. Historical missing
responses cannot be reconstructed by deploying this change.

Tests use real temporary SQLite execution databases and MockTransport upstreams.
They exercise complete, empty, exact-limit, oversized, interrupted, revoked and
invalid-MIME rejections, usage-shaped errors, restart/replay, job limits and
bounded worker/log errors. No supplier requests are made by these tests.
