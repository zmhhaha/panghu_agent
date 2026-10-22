# Isolated scientific Pi live model mechanism probe

This is an opt-in operational acceptance harness, not a literature research job.
Its local protocol self-tests and synthetic harness gates alone do not establish
model or production readiness. The initial draft made no live call; the parent
subsequently ran the isolated probe successfully on 2026-10-22 Asia/Shanghai.

## Recorded live result: 2026-10-22

Pod `scientific-pi-model-probe-20261022` completed with phase **Succeeded** on the
ARM64 acceptance derivative image, manifest digest
`sha256:944b3a3add1778b803fed88475d8e1ff171ef93873cd40c1859512e6dd27facf`.
The parent verified the sanitized result:

```json
{
  "valid": true,
  "code": "tool_roundtrip_verified",
  "pi_version": "0.73.1",
  "node_version": "24.21.0",
  "configured_alias": "llm-trusted",
  "upstream_model_observed": ["gpt-6.1-sol"],
  "model_requests": 2,
  "tool_names": ["bash"],
  "command_exact": true,
  "second_request_tool_marker": true,
  "marker_exact": true,
  "child_uid": 300000,
  "child_uid_cleanup": true,
  "real_key_absent": true,
  "fixture_mode": false,
  "synthetic_harness_gates": true,
  "production_acceptance": false
}
```

This is a selected-field transcription, not the complete report. The model name
is the SSE field reported through the gateway, not independent verification of
upstream identity. The live probe inherited the fixed scientific gateway SDK
compatibility profile and 8192-token model cap. NetworkPolicy allowed only DNS
and the model target. The existing scientific `llm-token` Secret reference fed
the root controller's proxy; the child received only the dummy loopback key.
No real research, mail, or report database writes occurred. No production
Deployment, default backend or production readiness flags changed; the human Pi
namespace was untouched. Independent production worker caller provisioning is
still pending. The parent collected sanitized evidence and removed all seven
validation resources after confirming Pod success; see the
[cleanup receipt](../../docs/scientific-research-pi/evidence/20261022-arm64/cleanup-receipt.json).

## Inputs and exact invocation

Use the dedicated worker image pinned by digest after the separate Linux
acceptance stage. Mount this repository's
`tests/scientific_pi/live/model_probe.py` read-only at
`/app/tests/scientific_pi/live/model_probe.py`; its source-root calculation expects
that location and the image's existing `/app` Python packages. The worker image
normally does not copy the test tree. Override the isolated Pod's command:

```sh
python -B /app/tests/scientific_pi/live/model_probe.py --report
```

The only required environment inputs are:

- `SCIENTIFIC_PI_IMAGE_DIGEST=sha256:<64 lowercase hex characters>` matching the
  actual dedicated image selected by the parent. The harness records this
  supplied digest; it cannot independently query the Pod specification.
- `SCIENTIFIC_PI_MODEL_API_KEY` from the existing **scientific-agent** namespace
  Secret `llm-token`, key `LLM_SERVICE_TOKEN`, using a Pod `secretKeyRef`. Do not
  copy a key into a command, ConfigMap, manifest literal, local file, or log.
  This temporary test deliberately uses the existing scientific caller; it does
  not provision or validate the future dedicated production worker caller.

The destination is compiled into the harness:
`http://llm-service.llm.svc.cluster.local:80/v1/chat/completions`, model alias
`llm-trusted`. There is no destination/path override, redirect following, generic
proxy, inherited HTTP proxy, or caller-registration action. No gateway restart is
needed. Do not pass production verification flags: the test sets them only on
its own in-memory RuntimeSettings to enter the actual non-fixture execution path.

## Isolated Pod boundary owned by the parent

Use a fresh disposable Pod with unique test labels in scientific-agent, with
NetworkPolicy restricted to DNS and the selected internal gateway endpoint. The
Pod must not have production app labels, service membership, report database,
mail settings, existing PVCs, human Pi mounts, container socket, or a service
account token. Do not alter existing Deployments or the human `pi` namespace.

Run a Linux root controller with read-only root filesystem, drop ALL capabilities
then add only CHOWN, DAC_OVERRIDE, SETUID, SETGID, KILL. The harness verifies the
exact effective capability mask. Use private memory-backed emptyDirs at `/data`
and `/tmp`; `/data` must be root-owned and allow task UID traversal (0755 is
sufficient). The harness creates fresh `/data/model-probe` (0711), a root-private
control directory/sentinel, and an attempt workspace assigned UID **300000**.
Reuse neither that Pod nor its scratch data; a pre-existing case directory fails.
Set Pod `activeDeadlineSeconds: 240` and a bounded termination grace. The runtime
job deadline is 180 seconds, upstream socket timeout is at most 90 seconds, and
request/response bodies are bounded to 1 MiB per round. At most two model requests
are accepted. Do not interpret this as strong general-purpose model-tool
containment: this probe uses a fixed prompt and tests its returned command after
execution; the runtime's bash tool itself is not command-allowlisted here.

## What the probe executes and records

The actual pinned Pi CLI is launched as:
`/usr/local/bin/node /opt/pi/node_modules/@mariozechner/pi-coding-agent/dist/cli.js`.
The runtime loads the existing reviewed C skill and manifest pins through
`runtime_from_env`. Only the test process monkeypatches C's prompt builder to ask
for exactly one bash invocation:

```sh
printf 'SCIENTIFIC_PI_REAL_TOOL_OK\n' | tee tool-roundtrip.txt
```

Pi then returns briefly with done. No search, literature, private-data access,
mail, or environment inspection is requested. The test requires an SSE `bash`
tool name, the exact command in the second request's assistant tool-call record,
a second-request tool result containing the fixed marker, an exact marker file
owned by UID 300000, normal runtime tool/end events, non-fixture provenance, no
remaining child UID processes, and no runtime/launch journals. It scans bounded
workspace files, provenance, and events for the real key without printing it.

The real credential stays in the root controller's memory. Pi receives only a
dummy key and a custom loopback provider URL. A root ThreadingHTTPServer on
127.0.0.1 accepts only the dummy authorization and exact chat-completions path,
then injects the real scientific credential server-side into the fixed outbound
request. It forwards streaming response chunks with immediate flushes. It never
logs headers, bodies, model text, tool arguments, or provider errors. Tool
arguments are parsed transiently from the return request to check equality;
only a boolean is retained. Tool names and bounded SSE `model` identifiers are
retained. The observed model field is gateway self-report and can itself be the
configured alias; it is distinct from independent upstream identity proof.

Output is sanitized JSON on stdout, optionally also `/data/report.json` (0600,
created exclusively). Failures produce `valid:false`, a controlled code and fixed
message, and a nonzero exit; raw exceptions and provider diagnostics are withheld.
A successful result explicitly reports `synthetic_harness_gates:true` and
`production_acceptance:false`. It proves only this isolated operational roundtrip.
It does not approve a production deployment, scientific outputs, all egress,
future dedicated caller provisioning, or broad cross-task isolation.

## Local checks without model calls

```sh
python -B tests/scientific_pi/live/model_probe.py --help
python -B tests/scientific_pi/live/model_probe.py --self-test
python -B -m py_compile tests/scientific_pi/live/model_probe.py
```

The embedded self-test parses SSE one byte at a time (including CRLF), verifies
that tool arguments are not retained, distinguishes observed model identifiers,
checks marker/exact-command observations, and rejects wrong aliases, a third
request, and oversized responses. It opens no socket and starts no Pi process.
