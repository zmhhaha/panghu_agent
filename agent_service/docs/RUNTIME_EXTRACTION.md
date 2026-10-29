# Neutral Agent runtime extraction

Recorded 2026-10-26, Asia/Shanghai.

The provenance and test results below describe the initial extraction. The
capsule implementation subsequently aligns the adapter's UID checks with the
host's inclusive `65536..2147483647` reserved range; its extracted-byte hash is
historical. Current host/runtime evidence is recorded in
[CAPSULE_VALIDATION_20261026.md](CAPSULE_VALIDATION_20261026.md).

This implementation lives on `codex/agent-execution-service`, created from
`origin/main` at `64a61883587a5f5a55dfeb8dd6c4ced26ed34271`. It does not require
checking out, merging or importing the scientific development branch. The
existing Pi and PPT applications are unchanged.

## Source provenance

The process implementation was extracted from this repository's
`panghu_agent` commit `d208aee9d78a56867cf8706eea4223e16eba046d`. The source files
had no local modifications when copied. Source names below are historical
provenance, not installed Python dependencies.

| Original source | Git blob at source commit | Destination |
| --- | --- | --- |
| `pi_worker/runtime/adapter.py` | `e74a494962214c4486b80e9ca9c317f96868fe05` | `agent_service/runtime/adapter.py`, neutralized |
| `pi_worker/runtime/launcher.py` | `3cdc15c8745d0a0a6a9b92a0ce76ab5f7ee18101` | `agent_service/runtime/launcher.py`, unchanged bytes |
| `pi_worker/runtime/windows_job.py` | `e5721f45193a59ae10683354a6ccae59de2825ba` | `agent_service/runtime/windows_job.py`, unchanged bytes |
| `pi_worker/runtime/jsonl_compactor.mjs` | `68552a5eb025663425c46b46970ff1d81658b82f` | `agent_service/runtime/jsonl_compactor.mjs`, unchanged bytes |
| `pi_worker/runtime/profiles.py` | `62d65ace75ec0599d99a69a8a2d4dc13acd95d7e` | neutral interface subset in `agent_service/runtime/profiles.py` |
| `scientific_agent/pi_contracts.py` | `7bb9ff32d64b60c37676d0e4e9b9777800934ada` | only local error/UTC helpers in `agent_service/runtime/contracts.py` |
| `ppt_service/worker/hermes_client.py` | `01897312c06787b8de59e175d4cf95e4b6c7b2cb` | `agent_service/hermes_client.py`, unchanged bytes |
| `tests/scientific_pi/runtime/fake_pi.py` | `4db6a668a9528c4b2ab5300becd4fac3d558cad6` | `tests/agent_service/fixtures/fake_pi.py`, unchanged bytes |

Working-tree SHA-256 values recorded at extraction time follow. Git's normal
line-ending conversion can change a checkout's byte hash; the Git blob IDs
above identify the source independently of that conversion.

| File | SHA-256 |
| --- | --- |
| Source adapter | `84cf285f3f0bf337ee6c86d21e9898667103b5f2ff04c212bfdc41a551721a0c` |
| Extracted adapter | `ae5ca43754ee4319825384dbc67b2bf3ed7871295398844e81d7eb8eb3fb2050` |
| Unchanged launcher | `e4725a718b8045cd2d8d328bb1e5f2f856bb514f269a4c333aeff1068facce34` |
| Unchanged Windows Job helper | `eef25da41e65f2276f7e4017ea1ba7446f53091b63a51bb641bc6a4a501eb085` |
| Unchanged JSONL compactor | `4ffaeac575e82d149b7104a6e48ff928dbf5a9cd77232ba22cd6415fbb0a8d89` |
| Source profiles | `47c27f3c2a082eee78f8d73127909ccf23abbd25672efb9aa397d1737fe57949` |
| Neutral profiles | `2106d5e8c67b84042bcc6cae25de213de2f6f00427b0393b1334d6ac6803b94b` |
| Source business contracts | `b87455c90a61fe78ca2946758e0d5e2115415d88bb44e527ccfb6cdc9471bc4f` |
| Local runtime helpers | `e0f8c6f08e09446e0fb00c412f504e156b4674074b99416780b1b73dcf58a456` |
| Unchanged Hermes client | `3db97c728d249e67b9e8099c0ccac32a05013b05e6038464187e2de31654abad` |
| Unchanged fake Pi fixture | `af8b93379d4d5c990c9edf05c8533eaadff0d3c01d3e136c2aa421520779b632` |

## Deliberate interface changes

- `PiRuntime(..., profile=...)` requires a trusted `ExecutionProfile` instance.
  There is no business default, research job parsing, domain revision switch or
  implicit revision capability. The profile protocol and `CommonExecution` are
  local to `agent_service.runtime`.
- `ContractError`, `parse_timestamp` and `utc_now` are small local helpers.
  Timestamps retain the UTC-aware RFC3339 requirement. No business schema is
  imported to obtain them.
- Custom model configuration uses `AGENT_MODEL_API_KEY`. The provider API mode
  remains explicit, including `openai-responses`. Model/provider/command settings
  remain trusted host configuration, never job-selected executable data.
- Cgroups use `agent-pi-<32 hex characters>`. Recovery rejects other cgroup
  namespaces; old service journals and process identities are not migrated.
- The optional skill-loading profile flag is named `load_skills`. Neutral text
  profiles can disable skill injection while retaining fixed runtime-resource
  integrity checks. Profile provenance contains execution metadata only.
- Driver/client and profile imports are entirely inside `agent_service`.
  The test executable is copied into the Agent service's own fixture directory.

The adapter extraction diff leaves the UID allocation requirements, privilege
drop launcher, capability clearing, NNP, parent-death handling, process limits,
pidfd/UID cleanup, optional cgroup containment, journal recovery, Windows fixture
Job Object, fresh HOME, bounded output/event handling and cancellation/deadline
logic in place. The launcher, Windows helper, JSONL compactor, Hermes client and
test executable match their source byte hashes.

## Validation and limits

From the independent worktree root:

```powershell
python -m pytest tests/agent_service/test_engines.py tests/agent_service/test_engine_profiles.py -q -p no:cacheprovider --tb=short
```

Result: **33 passed, 28 subtests passed, no skipped or failed cases**, 3.35 seconds
on Windows. These are one test run, not an accumulated total. Coverage includes:

- actual loopback Hermes HTTP protocol, durable admission intent fixture,
  single paid-admission attempt, polling the original run and uncertain-response
  handling;
- actual neutral PiRuntime executing the local subprocess fixture, metadata-only
  events, fresh workspace and fixed input preparation, explicit Responses model
  configuration and the neutral model-key name;
- cancellation, immutable deadlines, failed-run known usage with unknown cost,
  mandatory host stop evidence and quarantining missing/mismatched proof;
- required server profile, strict UTC helpers and cgroup recovery namespace;
- a fresh Python process that rejects imports of all former business packages,
  while importing the neutral runtime, drivers, profiles and Hermes client.

No external model or supplier was called. The fake CLI does not connect to the
configured loopback model URL. Local HTTP protocol tests also use fixed responses.
The production `WorkspaceTaskProfile.production_ready` remains false. These
Windows fixtures do not prove Linux/ARM64 deployment, K8s supervision or real
provider/tool round trips; production still requires the reviewed host runtime,
isolated identities and valid StopController evidence. Execution completion
continues to grant no business acceptance or publication authority.
