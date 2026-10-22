# Scientific Pi runtime local verification

This document preserves evidence from successive implementation stages. Earlier
"remaining gates" and "no production deployment" statements describe their
recorded stage, not the current rollout. See the
[current deployment and acceptance record](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md)
for deployed images, credentials, policies and outstanding work.

Work package A initially recorded local-only evidence on 2026-10-22 Asia/Shanghai. No commits, deployment, private credentials, shared requirements or Dockerfile changes were performed by A in that initial stage. Windows adapter fixtures passed then. Later isolated Linux image and live-model evidence from the parent is recorded below; production acceptance remains gated.

## Reviewed package and protocol

Exact independently selected npm package: `@mariozechner/pi-coding-agent@0.73.1`, MIT, upstream repository `badlogic/pi-mono`, npm metadata gitHead `781152fc24841dc54b22284514604048ebe5e2c9`. This is not an assertion that the existing human Pi deployment uses this package/version, nor that it is interchangeable with `@earendil-works/pi-coding-agent`.

Commands executed locally:

```text
npm view @mariozechner/pi-coding-agent version dist.tarball engines --json
npm pack @mariozechner/pi-coding-agent@0.73.1 --pack-destination pi_worker/runtime --json
npm view @mariozechner/pi-coding-agent@0.73.1 dist.integrity dist.shasum gitHead --json
```

The first pack attempt failed because the new runtime directory did not exist; creating the assigned package directory resolved it. No global npm or pip installation occurred. The downloaded tarball and selected review extracts were removed after inspection.

Registry metadata:

- tarball: <https://registry.npmjs.org/@mariozechner/pi-coding-agent/-/pi-coding-agent-0.73.1.tgz>
- sha1: `a14f26f45d3553556149432d743242675771862a`
- integrity: `sha512-gXQh3SaZmWTfVMc4Ao5+LGbVeKvzyO7tolok0nLsZgq9nGjZx/EEU3NM8C+qUnB4Nvs2rswG5qOVgLzQkq0fHQ==`
- package engines declaration: Node `>=20.6.0`; local host Node `v24.21.0`, Python `3.13.5`, npm `11.19.0`. Local host versions are not container image provenance. At that initial review stage no image had been built; later measured image evidence appears below.

The registry web-fetch helper was blocked because DNS resolved to a non-public address. npm metadata and tarball download succeeded, allowing actual published package source review. The inspected package files were `dist/cli/args.js`, `dist/main.js`, `dist/modes/print-mode.js`, `dist/core/resource-loader.js`, `dist/config.js`, `dist/core/model-registry.js`, `dist/core/resolve-config-value.js`, `docs/json.md`, `docs/models.md`, `docs/skills.md`, and the package manifest. Re-download the exact tarball to reproduce review; upstream [JSONL documentation](https://github.com/badlogic/pi-mono/blob/781152fc24841dc54b22284514604048ebe5e2c9/packages/coding-agent/docs/json.md) and [custom models documentation](https://github.com/badlogic/pi-mono/blob/781152fc24841dc54b22284514604048ebe5e2c9/packages/coding-agent/docs/models.md) provide context.

Verified in published source:

- `--print --mode json` selects noninteractive print mode; stdout contains session/event JSONL. stdin is closed with DEVNULL.
- `--no-session` prevents session persistence. `--no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --offline` disable ambient discovery/startup network operations. Explicit `--skill` paths survive `--no-skills`: resource loader lines 292–294 merge explicit CLI/additional skills in that branch.
- `--provider` and `--model` select the configured model; each completed assistant event supplies observed provider/model. The adapter rejects mismatch rather than changing providers.
- Print mode checks assistant error stop reasons for text output; JSON output can return zero after an assistant failure. The adapter checks `stopReason=error|aborted` itself and requires completed assistant plus agent-end events.
- `PI_CODING_AGENT_DIR` redirects the agent configuration directory. Each attempt receives a new HOME and its own agent directory, cwd and temporary directory.
- Custom providers use fresh `models.json` with `providers[provider]={baseUrl,api,apiKey,authHeader,models:[{id}]}`. `openai-completions` and `openai-responses` are distinct supported APIs. The adapter supports those explicit API names only, writes `apiKey: "SCIENTIFIC_MODEL_API_KEY"`, and passes the dedicated value through the child environment. No literal key is written to disk. Published value resolution resolves a named environment variable. Missing keys, malformed endpoint configuration and credential-bearing URLs fail closed.

A performed the public-source review and fake-executable tests. Subsequently the parent installed the pinned local npm tree and F reported its [real CLI smoke test](../tests/scientific_pi/integration/test_real_pi_smoke.py) passed in 3.337 seconds using Pi 0.73.1, Node 24.21.0 and a loopback-only synthetic OpenAI-completions SSE endpoint. It exercised an actual bash tool call and verified the tool result was sent in the second request. Windows suspended Job Object launch also passed this smoke. This is actual installed-CLI/tool protocol evidence with synthetic responses; it is not a real-provider/scientific-gateway conversation, production isolation acceptance or research-quality evidence. At that stage E still had to verify the locked transitive dependencies, Node and actual built image digest; later image evidence is recorded below. The top-level package pin alone does not lock its caret dependencies.

## Integration contract

[Runtime exports](../pi_worker/runtime/__init__.py) provide `PiRuntime`, `RuntimeSettings`, `RuntimeExecutionError`, `PI_PACKAGE`, and `PI_VERSION`.

```python
runtime = PiRuntime(settings)
runtime.recover(store.execution_uids())  # under exclusive worker instance lock
runtime.execute(job, workspace, event_sink, cancel_event)
```

`execute(job:dict, workspace:Path, event_sink:Callable[[dict],None], cancel_event:threading.Event)->None` writes outputs into an initially empty attempt workspace and creates controller-owned `provenance.json` only after successful process-tree fencing. Internal `attempt` and `_runtime_uid` fields are stripped before shared `ResearchJob` validation; all remaining wire fields are strict. Revisions must match accepted job configuration. Errors expose sanitized `code`, `message`, `retryable`; raw stderr/tool arguments/model text are discarded rather than emitted as operational events.

[Settings](../pi_worker/runtime/adapter.py) include:

| Setting | Meaning |
|---|---|
| `command` | Trusted argv tuple; prefer absolute Node binary and absolute installed Pi `dist/cli.js`. No shell. |
| `node_command` | Version-probe argv tuple, usually absolute Node binary. |
| `provider`, `model`, `expected_pi_version` | Explicit identities; expected version defaults to `0.73.1`. |
| `model_base_url`, `model_api` | Optional explicit custom endpoint/API pair; no private HOME configuration. |
| `model_compat`, `model_max_tokens` | Trusted model-level custom-provider configuration; compatibility keys/types are whitelisted and token limits must be integers 1..8192. Not selectable by user jobs. |
| `model_env` | Explicit worker model credentials only. Control/Vault/Kubernetes/SMTP/Redis/SQLite keys and loader hooks are rejected. |
| `skill_paths`, `skill_hashes` | Tuples of selected reviewed local skill files and SHA256s. |
| `bundle_manifest`, `bundle_manifest_sha256` | Pinned manifest; runtime verifies its revision and every local resource hash, including the task template. Required in production. |
| `skill_bundle_revision`, `runtime_revision` | Immutable accepted-job revisions. |
| `tool_pythonpath` | Immutable source checkout for C's `python -m pi_worker.research_tools` CLI. |
| `control_dir`, `denied_paths` | Root-private 0700 controller directory plus real existing protected-file sentinels that child must fail to open. The controller directory is automatically tested too. |
| `capability_verified`, `model_verified`, `isolation_verified` | Default false. Explicit operator acceptance evidence gates; not automatically inferred from env presence. |
| `image_digest` | Required production `sha256:` digest, never fabricated. |
| `fixture_mode` | Default false. True enables local same-UID fixtures and marks provenance as fixture. |
| `term_grace_seconds`, `kill_grace_seconds` | Bounded TERM then KILL/verification intervals, each at most 10 seconds. |
| `idle_timeout_seconds`, `max_output_bytes`, `max_line_bytes` | Bound silent/interactive hangs, cumulative stream output and individual JSONL allocation. Large valid Pi event lines are parsed internally; emitted metadata is small and excludes raw messages. |
| `cgroup_root` | Optional delegated cgroup v2 subtree with `cgroup.kill`; default production cleanup uses unique UID sweep. |

`run_uid_start`/`run_gid` are compatibility settings, not an allocator: B supplies each durable, never-reused UID as `_runtime_uid`; the child GID equals this UID. The worker must serialize executions and call recovery before admission. `.ready` delegates to `.health()['ready']`; readiness verifies all skill/resource hashes and probes the actual configured Pi and Node executables before acceptance. Version probes are cached for at most 30 seconds, invalidated immediately by executable/script stat changes; resource hashes are rechecked on every readiness call. Each probe has a five-second timeout and reads at most 101 bytes per stream, accepting only one consistent exact version from stdout or stderr with no other text. This handles Pi's deliberate stdout takeover, which routes its version to stderr. Readiness still requires explicit operator model/isolation acceptance evidence and is not a live model probe. Execution independently probes actual Pi and Node versions before spawn.

The trusted factory now writes model `maxTokens: 8192` and, for scientific gateway `openai-completions`, exactly `compat: {supportsStore:false, supportsUsageInStreaming:false, supportsReasoningEffort:false, maxTokensField:"max_tokens"}`. The pinned SDK otherwise defaults to unsupported `store`, `stream_options`, `max_completion_tokens` and a custom-model limit of 16384; the gateway forbids extra fields and caps `max_tokens` at 8192. The [strict-wire regression](../tests/scientific_pi/runtime/test_gateway_compat.py) runs the actual pinned CLI against loopback responses and validates both requests using the real gateway ChatRequest schema without importing its app or credentials. It passed on Linux. `openai-responses` receives the same token cap without completions compatibility. Reviewed direct providers may explicitly select `SCIENTIFIC_PI_MODEL_COMPAT_PROFILE=none`; arbitrary profiles/fields/types are rejected. The live probe inherits this trusted configuration.

C integration: original local [research procedure](../scientific_skills/research-procedure/SKILL.md), [task template](../scientific_skills/task-template.md), and [bundle manifest](../scientific_skills/manifest.json) are selected. The prompt comes from C's `build_research_prompt(job)`. No K-Dense content was vendored or claimed verified. Model input omits user identity/email/control metadata; user topic remains data inside the reviewed prompt template.

Child Linux PATH is immutable `/usr/local/bin:/usr/bin:/bin`, allowing image Node/Python. No controller environment is inherited beyond explicit credentials; Windows fixture mode additionally keeps essential OS variables and host PATH for local executables. Production must use the immutable source/image and controlled network policy.

## Production process and file boundary

Production fails closed outside Linux root control mode or with missing acceptance evidence, image digest, reviewed skills/resources, sentinel paths or pidfd support. A nonroot dropALL controller cannot switch UID and is rejected; it is not silently accepted as isolation.

The implemented root [launcher](../pi_worker/runtime/launcher.py) uses `setgroups([])`, `setresgid(uid,uid,uid)`, `setresuid(uid,uid,uid)`, clears effective/permitted/inheritable capabilities with `capset`, verifies effective/permitted/inheritable/ambient capabilities are zero, and sets `PR_SET_NO_NEW_PRIVS`. It verifies protected paths are denied after the drop. Parent-death SIGKILL is installed before privilege changes, reinstalled after Linux clears it on UID/GID changes, and parent PID is checked to prevent an orphan root launcher escaping UID-based recovery.

B must persist each UID allocation before dispatch, reserve the UID range exclusively for this worker, never reuse it during retention, create traverse-only task parents, and provide a fresh attempt directory. Runtime temporarily takes root ownership of each directory, applies mode 0700 while root owns it, then assigns the unique child UID/GID. This also handles a workspace already assigned to the child by B and does not require CAP_FOWNER. Other attempts and control/results remain inaccessible. Root controller needs explicitly reviewed `SETUID`, `SETGID`, `KILL`, `CHOWN`, and `DAC_OVERRIDE` capabilities with container seccomp/NNP restrictions; Pi's child capabilities are empty. This is a deployment/design exception for the parent to approve and document, not a claim the original dropALL plan works.

After TERM to the root process group, KILL and root wait, the controller scans `/proc` for all real/effective/saved/filesystem IDs matching the unique UID, including detached descendants. It uses pidfds plus an ownership recheck to avoid signaling a reused unrelated PID, repeats bounded KILL sweeps, and requires two empty live-process scans. Zombies have no executable activity and are excluded; root Pi itself is reaped. Unreadable process credentials, remaining live processes or failed stop verification raise `process_safety_unverified`, retain the journal and fence readiness. The parent scheduler must then stop admitting work. Recovery sweeps all B-persisted UIDs and runtime journals before queue requeue; unknown fixture/orphan records fail closed rather than inferring death from a PID.

The launcher applies `RLIMIT_NPROC` soft and hard limits from `max_processes` before dropping UID, preserving any tighter inherited hard limit. With a dedicated nonroot UID this limits the attempt's processes even without cgroups. Optional cgroups additionally contain fork/detach races and enable `pids.max`; they are not mandatory and no hostPath or cgroup mount permission is assumed. Container memory/CPU/resource limits are still deployment requirements; UID sweep is the process-fencing mechanism. Acceptance must verify `/proc` visibility and pidfd support in the actual Pod PID/user namespaces. The UID model assumes no shared writable privileged helper or other same-UID service, and protected source/control filesystem permissions must be tested. It does not implement network isolation; E/F own actual ingress/egress enforcement and acceptance.

Windows fixtures use `CREATE_SUSPENDED`, assign the still-suspended process to a Job Object, and resume via `NtResumeProcess` only after assignment. This prevents descendants from starting before containment. Cleanup verifies zero active job processes after termination and retains uncertainty on assignment/verification failure. These fixture mechanics do not establish production filesystem/identity isolation. POSIX same-UID fixtures use process groups and do not claim to contain deliberately detached hostile children; this mode is rejected for production.

Controller output writes use exclusive randomly named temporary files, no-follow where available, fsync and rename; preexisting model-controlled temporary symlinks cannot redirect root writes. Reports and other model outputs still require B/C's independent artifact/structure validation.

## Provenance

Runtime overwrites model-supplied provenance with observed assistant model/provider, actual version probes, configured verified image digest/revisions, `token_usage: null`, fixture marker and UTC retrieval interval. Retrieval start/end currently bound the execution as `retrieval_interval_basis: "execution_envelope"`; C's individual search records provide exact per-request timestamps. It does not claim token counts or actual retrieval happened when none did. Unknown fixture image digest is null, never an invented digest. Pi, Node and immutable image values were subsequently measured in the isolated Linux image below; production rollout and caller acceptance remain separate.

## Local verification and outstanding gates

```text
python -m unittest discover -s tests/scientific_pi/runtime -p 'test_*.py' -v
```

Earlier Windows scoped run: **19 tests, 18 passed, 1 skipped**, approximately 6.5 seconds, Python 3.13.5 on Windows. Additional audit regressions cover process quota enforcement and preservation of inherited limits, root-ownership chmod ordering without FOWNER (mocked, not Linux syscall acceptance), readiness hashes/executable/version checks and caching, exact stderr versions, noisy version rejection, and overlarge version output termination. Skip: the host cannot create symlinks without additional privilege; the exclusive temporary-file implementation was inspected but the symlink fixture did not execute on this host.

The [fixture executable](../tests/scientific_pi/runtime/fake_pi.py) and [tests](../tests/scientific_pi/runtime/test_runtime.py) cover success/provenance, structured metadata events, no inherited worker/Vault/human model key or Node loader controls, fresh HOME/cwd, explicit reviewed skill flags/hashes, custom endpoint env-reference configuration, normal root exit with a lingering child, cancellation with a child process, in-flight deadline, malformed JSONL, output limits, idle hangs, nonzero exit, assistant error despite zero exit, wrong model/version, incomplete event streams, event consumer failure, unknown orphan refusal and degraded fencing after uncertain cleanup.

## 2026-10-22 isolated Linux image evidence

The parent's final ARM64 rebuild passed **146 tests with zero failures/skips**, including **27 runtime tests** (actual pinned CLI strict-wire loopback and group cleanup); an additional **7 Linux OS isolation tests passed in 7.204 seconds**. Pi is 0.73.1, measured Node is 24.21.0, and the pinned Python base is Python 3.13/trixie. This is ARM64 evidence only; all current nodes are ARM64 and no amd64 build is claimed.

- Worker manifest digest: `sha256:dbdf5c05fdf669b16ed065fbbe19f094244542e2a26639c880115ac284e6a04b`; config ID: `sha256:46ec347ce3080a6f53f2fff29d2450a2a582eb7f6e14c0175f9c4299bb295869`.
- Acceptance derivative manifest: `sha256:944b3a3add1778b803fed88475d8e1ff171ef93873cd40c1859512e6dd27facf`; config ID: `sha256:76b30d39df6b6f4146fe892bec6bd27097092857c0508b47a320702b6e28ff6d`.
- API manifest: `sha256:89caf56457ee7f778ebe26337278ca3ede6bf6550d7f6339386473c18dcfbaa9`. Actual read-only Linux import with no network or extra capabilities passed using a synthetic CrewAI package whole-directory read-only bind mount to reproduce the existing overlay. Image Pi modules resolve from fixed `/opt/scientific-pi-extension`, after mounted CrewAI paths; no production Deployment was patched.
- Node base: `node@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20`; Python base: `python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f`.
- API base: `arm-cluster-master:5000/agent-api@sha256:62f908474e4df08436ea64f5a54f663a11cdadb5b304c1675bcf5c77de035674`.
- Curated source archive SHA256: `87d68c0f93ed2536e942e4ffb749af8ab97fde5df381b58107570b88b58e23a5` (not a Git commit).

A pre-final Linux suite exposed a same-UID fixture race: its root had exited, so waiting for that root after killpg did not confirm the orphaned descendant had stopped. The fix adds Linux fixture-only process-group `/proc/stat` checks, bounded repeated group KILL and two consecutive scans without active members; zombies are excluded and uncertainty fences the runtime. It signals only the original fixture process group, never all root-UID processes. Production UID/pidfd/cgroup behavior and the immediate descendant assertion are unchanged. The final Linux suite passed after this fix. New Windows strict-CLI preflight still fails its five-second version deadline; direct CLI also did not return within twelve seconds. This unresolved Windows issue is retained, not skipped or used to extend the production timeout, and does not negate the earlier Windows smoke record.

The separate [live model probe](scientific-pi-live-model.md) subsequently succeeded in Pod `scientific-pi-model-probe-20261022` on the acceptance derivative: `valid:true`, `tool_roundtrip_verified`, two model requests, actual bash/exact command/marker and second-request tool marker, UID 300000 cleanup, and real-key absence checks passed. Configured alias was `llm-trusted`; SSE reported `gpt-6.1-sol`, a gateway self-report rather than independent upstream identity proof. The root proxy held the existing scientific caller key; Pi received a dummy loopback key. NetworkPolicy allowed only DNS and the model target. No real research, mail, or report database writes occurred. `fixture_mode=false` used real Linux UID execution; `synthetic_harness_gates=true` and `production_acceptance=false` explicitly remain in the result.

Historical remaining acceptance gates at that early isolated-image stage:

1. Provision and register an independently scoped production worker caller through the existing Vault/gateway process. The successful probe used the existing scientific caller, not that future credential.
2. Review production policy, source permissions, resources, retained UID allocation and rollout/recovery configuration. The measured image and isolated OS checks do not automatically accept a production deployment; no default readiness flags were changed.
3. Verify the production worker's complete ingress/egress behavior, including required public academic HTTPS access and all denied destinations; the live probe tested only DNS/model egress.
4. Validate real scientific outputs and publication integration. Synthetic reports and a fixed operational bash marker are not research-quality evidence.

No production workload deployment, default-backend change, production verification-flag change, or remote OpenSpec mutation was made by those early acceptance steps. The existing human Pi namespace was untouched. These are historical scope statements; consult the current rollout record linked above for subsequent deployment actions.

## 2026-10-22 strict HTTP retry acceptance

The earlier adapter stopped Pi on the first failed assistant event and marked
every such error nonretryable. That also interrupted Pi's own session retry,
while the underlying OpenAI SDK could retry before emitting the first assistant
failure. The full HTTP retry gate was therefore incomplete at that stage.

A loopback-only probe against actual Pi 0.73.1 confirmed that HTTP 429 and 503
produce the exact fixture strings `429 controlled retry fixture` and
`503 controlled retry fixture`. With task-local settings
`retry.enabled=false` and `retry.provider.maxRetries=0`, each execution made
exactly one HTTP request. No real model or provider credential was involved.
Evidence: outer-repository
`.research-temp/scientific-pi-http-error-shape-20261022.json`, Pod UID
`8ef5fb32-8d3a-46f5-b63d-df3554f7c86c` (`Succeeded`).

The adapter now writes those fixed settings into every fresh attempt's agent
directory and leaves retry ownership with the scheduler. Only an assistant
`stopReason=error` whose error string starts with the observed status format for
**429, 500, 502, 503 or 504** becomes `model_http_transient` with `retryable=true`.
The public error message contains only the allowed numeric status and fixed text.
Status-like numbers elsewhere in a body, authentication failures, unknown
provider failures, protocol errors and aborted requests remain nonretryable.
This does not determine the cause of the earlier production stream error.

After the first failed attempt's entire process tree is confirmed stopped, a
fixed two-second `cancel_event.wait` backs off before the error reaches the
scheduler. It is clipped by the original task deadline and stops immediately on
cancellation; it does not reset that deadline or make another request itself.
The existing scheduler admits at most two attempts, with separate workspaces,
HOME directories and never-reused task UIDs. A second failed attempt is terminal.
No scheduler, queue-store or shared-gateway change was required for this policy.

The [focused retry suite](../tests/scientific_pi/runtime/test_model_retry.py)
passed **7 tests, zero skips, in 32.673 seconds on ARM64**. It used acceptance
image `sha256:7cd0abcae3cdf583585c0876b50b2cc37a3b15e377953eebd18926d245eefcfc`
with the updated adapter and new test file mounted read-only; this is source
overlay evidence, not a claim that the base image already contains this fix.
The cases establish:

- Actual Pi HTTP 429 then HTTP 503 yields exactly two requests and two fresh
  scheduler attempts, followed by terminal failure with no report.
- Actual HTTP 401 with `503` in its body yields one request and no retry.
- Actual HTTP 503 followed by a successful second attempt runs the deterministic
  research fixture through real Pi tools and validation, then publishes once,
  sends one acknowledgement, and downloads the exact report bytes without mail.
- The precise status allowlist, permanent-error rejection, cancellation during
  backoff, and deadline exhaustion all pass locally and in the ARM64 suite.

Evidence: outer-repository
`.research-temp/scientific-pi-http-retry-final-20261022.json`, Pod UID
`ae5e0bdd-8892-470d-a7b6-57ff90b111b0` (`Succeeded`). That Pod had no service-account
token, production volume or credential reference, and its NetworkPolicy denied
all ingress/egress; its HTTP fixtures used loopback. The Pod, source ConfigMap
and NetworkPolicy were deleted after log collection, as were the earlier shape
probe's three resources. Production rebuilding and rollout remain recorded by
the deployment coordinator in the linked rollout record.

The separately added sixth JSONL transport regression also passed on this same
acceptance image with its latest test file mounted read-only: **6 tests, zero
skips, in 0.981 seconds**. Log:
`.research-temp/scientific-pi-compactor-final-20261022.json`, Pod UID
`16a0b1c2-247d-4e34-992f-c0010fb37aa7`; all three temporary resources were removed.
